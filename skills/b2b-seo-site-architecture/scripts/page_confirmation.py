"""Durable, revision-specific user decisions and the context shared with website builders."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from plan_records import Row, index, obj, records, strings, text
from register_project import FILES, atomic_write, project_lock
from revisions import read_delivery_rows
from seo_project import local_file, project_for
from workbench_decisions import ACTIONS, pending_resolution, proposed_resolution, user_text
from workbench_events import DECISIONS, SCHEMA, SEO_CHOICES, invalidate_alignment, normalize_handoff as normalize_handoff, utc_now

FILENAME = "workbench-handoff.json"


class DecisionConflict(ValueError):
    """Another editor changed this page or the browser belongs to another revision."""


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def populate_planning_context(folder: Path, pages: list[Row]) -> None:
    """Materialize the original assignment and a concrete business question once per compiled revision."""
    by_id = index(pages)
    members: dict[str, list[str]] = {pid: [] for pid in by_id}
    questions: dict[str, str] = {}
    if (folder / "keyword-decisions.csv").is_file():
        for row in read_delivery_rows(local_file(folder, "keyword-decisions.csv")):
            if row.get("page_id") in members and row.get("keyword_id"):
                members[row["page_id"]].append(row["keyword_id"])
    if (folder / "page-opportunities.csv").is_file():
        for row in read_delivery_rows(local_file(folder, "page-opportunities.csv")):
            if row.get("status") == "pending" and row.get("question") and row.get("page_id") in by_id:
                questions.setdefault(row["page_id"], row["question"])
    for pid, page in by_id.items():
        page["assigned_keyword_ids"] = members[pid]
        gaps = strings(page, "fact_gaps") if page.get("state") == "conditional" and page.get("fact_gaps") else []
        page["business_question"] = questions.get(pid) or (gaps[0] if gaps else "")


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
    populate_planning_context(folder, pages)
    ids = [p["id"] for p in pages]
    if not ids or not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("Expected unique planned page IDs")
    resources = {key: name for key, name in FILES.items() if name != FILENAME and (folder / name).is_file()}
    handoff: Row = {
        "schema": SCHEMA, "revision_id": str(uuid4()), "change_number": 0, "updated_at": None,
        "project": {key: project.get(key) for key in ("name", "site_url", "market", "language", "mode", "scope_note")},
        "use": {
            "build": "本次纳入建站或内容更新。按 page_action 与实际网站决定新建、复用或修改。",
            "skip": "本次跳过此页。按 demand_resolution 读取用户确认的词组去向，逐词原表保留原规划。",
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


def contextual_handoff(folder: Path, data: Row) -> Row:
    pages = records(data, "pages")
    if any("assigned_keyword_ids" not in p or "business_question" not in p for p in pages):
        populate_planning_context(folder, pages)
        data["pages"] = pages
    return normalize_handoff(data)


def read_handoff(folder: Path) -> Row:
    return contextual_handoff(folder, obj(json.loads(local_file(folder, FILENAME).read_bytes())))


PageEdit = Callable[[Row, list[Row]], bool]


def _edit_locked(folder: Path, root: Path, revision_id: str, page_id: str, update: PageEdit) -> tuple[Row, bool]:
    from register_project import sync_entry_locked

    path = local_file(folder, FILENAME)
    previous = path.read_bytes()
    data = contextual_handoff(folder, obj(json.loads(previous)))
    if revision_id != data["revision_id"]:
        raise DecisionConflict("此版本已变化，请读取原工作台后重试。")
    pages = records(data, "pages")
    page = next((row for row in pages if row["id"] == page_id), None)
    if page is None:
        raise ValueError("Unknown page ID")
    changed = update(page, pages)
    if changed:
        count = data["change_number"]
        if type(count) is not int:
            raise ValueError("Invalid stored change number")
        data.update(pages=pages, change_number=count + 1, updated_at=utc_now())
        data = normalize_handoff(data)
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


def mutate_locked(folder: Path, root: Path, revision_id: str, page_id: str,
                  update: Callable[[Row], bool]) -> tuple[Row, bool]:
    """Single-page update for writers already holding the project and workbench locks."""
    return _edit_locked(folder, root, revision_id, page_id, lambda page, _: update(page))


def _edit(folder: Path, revision_id: str, page_id: str, update: PageEdit) -> tuple[Row, bool]:
    """Synchronize authoritative page data and the project entry under the same writer lock."""
    folder = folder.resolve()
    root = project_for(folder) or folder
    with ExitStack() as locks:
        locks.enter_context(project_lock(root))
        if root != folder:
            locks.enter_context(project_lock(folder))
        return _edit_locked(folder, root, revision_id, page_id, update)


def mutate_handoff(folder: Path, revision_id: str, page_id: str,
                   update: Callable[[Row], bool]) -> tuple[Row, bool]:
    return _edit(folder, revision_id, page_id, lambda page, _: update(page))


def change_page(folder: Path, request: Row) -> Row:
    choices = {"decision": "decision_version", "seo_decision": "seo_version",
               "business_answer": "business_version", "demand_resolution": "demand_version"}
    inputs = request.keys() - {"revision_id", "page_id", "expected_version"}
    if len(inputs) != 1 or not inputs <= choices.keys() or not {"revision_id", "page_id", "expected_version"} <= request.keys():
        raise ValueError("Provide revision_id, page_id, expected_version and one user choice")
    field = next(iter(inputs))
    counter = choices[field]
    page_id = text(request, "page_id")
    version = request["expected_version"]
    if type(version) is not int or version < 0:
        raise ValueError("Invalid user choice version")
    revision = text(request, "revision_id")

    def remember(page: Row, key: str, value: object, now: str) -> None:
        ver = page[choices[key]]
        if type(ver) is not int:
            raise ValueError("Invalid stored user choice version")
        history = records(page, "choice_history")
        history.append({"field": key, "value": value, "at": now, "version": ver + 1, "origin_revision_id": revision})
        page.update({key: value, choices[key]: ver + 1, "choice_history": history})

    def select(page: Row, pages: list[Row]) -> bool:
        by_id = index(pages)
        choice: object = request[field]
        if field == "demand_resolution":
            choice = proposed_resolution(choice, page, by_id)
        elif field == "business_answer":
            choice = user_text(choice, field)
        elif choice not in (SEO_CHOICES if field == "seo_decision" else DECISIONS):
            raise ValueError("Invalid user choice")
        if page[field] == choice:
            return False
        if page[counter] != version:
            raise DecisionConflict("该选择已在另一窗口更新，请核对最新状态后再选。")
        now = utc_now()
        previous = page[field]
        remember(page, field, choice, now)
        if field in {"decision", "seo_decision"}:
            page["decided_at" if field == "decision" else "seo_decided_at"] = now
        if field == "business_answer":
            invalidate_alignment(page, now, revision)
        if field == "demand_resolution":
            old, new = obj(previous), obj(choice)
            if any(old[key] != new[key] for key in ("action", "target_page_id", "target_module_id", "keyword_ids")):
                for resolution in (old, new):
                    target = str(resolution["target_page_id"])
                    if resolution["action"] == "merge" and target in by_id:
                        invalidate_alignment(by_id[target], now, revision)
        if field == "decision" and (previous == "skip" or choice == "skip"):
            for owner in pages:
                resolution = obj(owner["demand_resolution"])
                target = str(resolution["target_page_id"])
                if owner["id"] != page_id and target != page_id:
                    continue
                if resolution["action"] != "pending":
                    remember(owner, "demand_resolution", pending_resolution(), now)
                    if target in by_id:
                        invalidate_alignment(by_id[target], now, revision)
                elif owner["id"] == page_id and previous == "skip":
                    # A form opened during the prior skip cannot commit after a later re-selection.
                    saved_version = owner["demand_version"]
                    if type(saved_version) is int:
                        owner["demand_version"] = saved_version + 1
        return True

    return _edit(folder, revision, page_id, select)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Save one user choice to the same revision used by the editable workbench.")
    parser.add_argument("--workbench", type=Path, required=True)
    parser.add_argument("--revision-id", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--expected-version", type=int, required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--decision", choices=DECISIONS)
    choice.add_argument("--seo-decision", choices=SEO_CHOICES)
    choice.add_argument("--business-answer")
    choice.add_argument("--demand-action", choices=sorted(ACTIONS))
    parser.add_argument("--target-page", default="")
    parser.add_argument("--target-module", default="")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    request: Row = {"revision_id": args.revision_id, "page_id": args.page_id, "expected_version": args.expected_version}
    if args.demand_action:
        request["demand_resolution"] = {"action": args.demand_action, "target_page_id": args.target_page,
                                        "target_module_id": args.target_module, "reason": args.reason}
    elif args.target_page or args.target_module or args.reason:
        parser.error("Use target and reason options with --demand-action")
    else:
        request.update({key: value for key in ("decision", "seo_decision", "business_answer")
                        if (value := getattr(args, key)) is not None})
    try:
        saved = change_page(args.workbench, request)
        selected = next(p for p in records(saved, "pages") if p["id"] == args.page_id)
        print(json.dumps({"revision_id": saved["revision_id"], "change_number": saved["change_number"],
                          "page": selected}, ensure_ascii=False))
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
