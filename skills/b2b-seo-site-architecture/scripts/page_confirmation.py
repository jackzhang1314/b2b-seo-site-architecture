"""Durable, revision-specific user decisions and the context shared with website builders."""
from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from plan_records import Row, obj, records, text
from register_project import FILES, atomic_write, project_lock
from revisions import read_delivery_rows
from seo_project import local_file, project_for
from workbench_events import DECISIONS, SCHEMA, SEO_CHOICES, derive_progress, normalize_handoff as normalize_handoff, utc_now

FILENAME = "workbench-handoff.json"


class DecisionConflict(ValueError):
    """Another editor changed this page or the browser belongs to another revision."""


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def create_handoff(folder: Path, project: Row) -> None:
    path = folder / FILENAME
    fields = ("id", "name", "name_en", "name_zh", "url", "parent_id", "page_type", "template_id", "variant",
              "objective", "page_action", "state", "approval", "primary_keyword", "boundary", "priority")
    lists = ("adjacent_page_ids", "module_ids", "comparable_urls", "fact_gaps")
    pages: list[Row] = []
    # Article topics have a separate content plan. Only real, planned core URLs appear in the confirmation table.
    for row in read_delivery_rows(local_file(folder, "pages.csv")):
        pages.append({**{k: row.get(k, "") for k in fields}, **{k: (row.get(k) or "").splitlines() for k in lists},
                      "decision": "pending", "decision_version": 0, "decided_at": None})
    ids = [p["id"] for p in pages]
    if not ids or not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("Expected unique planned page IDs")
    resources = {key: name for key, name in FILES.items() if name != FILENAME and (folder / name).is_file()}
    handoff: Row = {
        "schema": SCHEMA, "revision_id": str(uuid4()), "change_number": 0, "updated_at": None,
        "project": {key: project.get(key) for key in ("name", "site_url", "market", "language", "mode", "scope_note")},
        "use": {
            "build": "本次纳入建站或内容更新。按 page_action 与实际网站决定新建、复用或修改。",
            "skip": "本次不做此页。保留其规划与关键词归属，已有线上页面保持原状。",
            "pending": "用户尚未决定。保留为待定页面。",
            "revision": "用户提供的工作台链接选定本版。逐页 decision 是用户选择，approval 是上游规划状态。",
            "builder": "按 id 和 template_id 关联合适的模板、导航、模块与字段。父页未纳入时单列层级问题。",
            "onpage": "按 page_id 读取逐词归属、内容模块与同类页依据。业务事实仍按资料核实。",
        },
        "resources": resources, "pages": pages,
    }
    atomic_write(path, encode(normalize_handoff(handoff)), None)
    marker = 'name="workbench-revision" content="__WORKBENCH_REVISION__"'
    for name in ("index.html", "design.html"):
        view = folder / name
        html = view.read_text(encoding="utf-8")
        if marker not in html:
            raise ValueError(f"Missing workbench revision marker in {name}")
        view.write_text(html.replace(marker, f'name="workbench-revision" content="{handoff["revision_id"]}"', 1),
                        encoding="utf-8")
    (folder / "workbench-open.js").write_text("window.workbenchEditorUrl = null;\n", encoding="utf-8")
    launcher = folder / "打开可编辑工作台.command"
    launcher.write_text('#!/bin/sh\nexec python3 "${CODEX_HOME:-$HOME/.codex}/skills/b2b-seo-site-architecture/'
                        'scripts/workbench.py" --workbench "$(dirname "$0")" --open\n', encoding="utf-8")
    launcher.chmod(0o755)


def read_handoff(folder: Path) -> Row:
    return normalize_handoff(obj(json.loads(local_file(folder, FILENAME).read_bytes())))


def mutate_handoff(folder: Path, revision_id: str, page_id: str,
                   update: Callable[[Row], bool]) -> tuple[Row, bool]:
    """Synchronize authoritative page data and the project entry under the same writer lock."""
    from register_project import sync_entry_locked

    folder = folder.resolve()
    root = project_for(folder) or folder
    with ExitStack() as locks:
        locks.enter_context(project_lock(root))
        if root != folder:
            locks.enter_context(project_lock(folder))
        path = local_file(folder, FILENAME)
        previous = path.read_bytes()
        data = normalize_handoff(obj(json.loads(previous)))
        if revision_id != data["revision_id"]:
            raise DecisionConflict("此版本已变化，请读取原工作台后重试。")
        pages = records(data, "pages")
        page = next((row for row in pages if row["id"] == page_id), None)
        if page is None:
            raise ValueError("Unknown page ID")
        changed = update(page)
        if changed:
            count = data["change_number"]
            if type(count) is not int:
                raise ValueError("Invalid stored change number")
            derive_progress(page)
            data.update(pages=pages, change_number=count + 1, updated_at=utc_now())
        content = encode(data)
        atomic_write(path, content, previous)
        try:
            if root != folder:
                sync_entry_locked(root, folder, data)
        except (OSError, ValueError, KeyError):
            if content != previous:
                atomic_write(path, previous, content)
            raise
        return data, changed


def change_page(folder: Path, request: Row) -> Row:
    field = "seo_decision" if "seo_decision" in request else "decision"
    if set(request) != {"revision_id", "page_id", "expected_version", field}:
        raise ValueError("Provide revision_id, page_id, expected_version and one user choice")
    page_id, choice = text(request, "page_id"), text(request, field)
    version = request["expected_version"]
    allowed = SEO_CHOICES if field == "seo_decision" else DECISIONS
    if choice not in allowed or type(version) is not int or version < 0:
        raise ValueError("Invalid user choice or version")
    counter, date = ("seo_version", "seo_decided_at") if field == "seo_decision" else ("decision_version", "decided_at")
    revision = text(request, "revision_id")

    def select(page: Row) -> bool:
        if page[field] == choice:
            return False
        if page[counter] != version:
            raise DecisionConflict("该选择已在另一窗口更新，请核对最新状态后再选。")
        now = utc_now()
        choices = records(page, "choice_history")
        choices.append({"field": field, "value": choice, "at": now, "version": version + 1,
                        "origin_revision_id": revision})
        page.update({field: choice, counter: version + 1, date: now, "choice_history": choices})
        return True

    return mutate_handoff(folder, revision, page_id, select)[0]
