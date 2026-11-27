"""Durable, revision-specific user decisions and the context shared with website builders."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from plan_records import Row, obj, records, text
from register_project import FILES, atomic_write, project_lock
from seo_project import local_file

FILENAME = "workbench-handoff.json"
SCHEMA = "b2b-seo-workbench-handoff/1"
DECISIONS = ("pending", "build", "skip")


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
    with local_file(folder, "pages.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            pages.append({**{k: row.get(k, "") for k in fields}, **{k: (row.get(k) or "").splitlines() for k in lists},
                          "decision": "pending",
                          "decision_version": 0, "decided_at": None})
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
    atomic_write(path, encode(handoff), None)
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
    value = obj(json.loads(local_file(folder, FILENAME).read_bytes()))
    if value.get("schema") != SCHEMA or not isinstance(value.get("revision_id"), str):
        raise ValueError("Invalid workbench handoff")
    return value


def change_page(folder: Path, request: Row) -> Row:
    if set(request) != {"revision_id", "page_id", "expected_version", "decision"}:
        raise ValueError("Provide revision_id, page_id, expected_version and decision")
    page_id, decision = text(request, "page_id"), text(request, "decision")
    version = request["expected_version"]
    if decision not in DECISIONS or type(version) is not int or version < 0:
        raise ValueError("Invalid page decision or version")
    with project_lock(folder):
        path = local_file(folder, FILENAME)
        previous = path.read_bytes()
        data = obj(json.loads(previous))
        if data.get("schema") != SCHEMA or text(request, "revision_id") != data.get("revision_id"):
            raise DecisionConflict("此工作台版本已变化，请重新打开后确认。")
        pages = records(data, "pages")
        page = next((p for p in pages if p["id"] == page_id), None)
        if page is None:
            raise ValueError("Unknown page ID")
        if page["decision"] == decision:
            return data
        if page["decision_version"] != version:
            raise DecisionConflict("这页的选择已在另一个窗口更新，请核对最新状态后再选。")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        page.update(decision=decision, decision_version=version + 1, decided_at=now)
        count = data["change_number"]
        if type(count) is not int:
            raise ValueError("Invalid stored change number")
        data.update(pages=pages, change_number=count + 1, updated_at=now)
        atomic_write(path, encode(data), previous)
        return data
