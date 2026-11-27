"""Resolve a website's page work queue or record verified work in its selected revision."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from page_confirmation import FILENAME, mutate_handoff, read_handoff
from plan_records import Row, obj, records, text
from register_project import project_lock, resolve_workbench, sync_entry_locked
from revisions import read_revisions
from seo_project import identity, local_file, project_for
from workbench_events import TRACKS, append_event, event_payload


def directory(path: Path) -> Path:
    folder = path.resolve()
    if folder.is_file() and folder.name in {FILENAME, "index.html", "design.html", "seo-project-index.json"}:
        folder = folder.parent
    if not folder.is_dir():
        raise ValueError("Specify a local workbench directory")
    return folder


def resolve(*, workbench: Path | None, project_root: Path | None, site_url: str | None = None,
            market: str | None = None, language: str | None = None, mode: str | None = None,
            page_id: str | None = None, queue: str | None = None) -> Row:
    if workbench is not None:
        folder = directory(workbench)
        root = project_for(folder) or folder
    else:
        root = (project_root or project_for(Path.cwd()) or Path.cwd()).resolve()
        folder = root
    with project_lock(root):
        if workbench is None:
            folder, role = resolve_workbench(root, site_url=site_url, market=market, language=language, mode=mode)
        else:
            role = "standalone"
            entry = root / "AGENTS.md"
            if root != folder and entry.exists():
                revisions = read_revisions(root, local_file(root, "AGENTS.md").read_text(encoding="utf-8-sig"))
                selected = revisions.get(folder / "seo-project-index.json")
                if selected:
                    role = selected[0]
        context = read_handoff(folder)
        project = obj(context["project"])
        if workbench is not None:
            criteria = {k: v for k, v in (("site_url", site_url), ("market", market), ("language", language),
                                          ("mode", mode)) if v is not None}
            if identity(project) != identity({**project, **criteria}):
                raise ValueError("Requested site, market, language or mode differs from this workbench")
        if root != folder:
            sync_entry_locked(root, folder, context)
    pages = records(context, "pages")
    if page_id:
        pages = [p for p in pages if p["id"] == page_id]
        if not pages:
            raise ValueError("This workbench has no page with that ID")
    if queue == "build":
        pages = [p for p in pages if p["decision"] == "build"]
    if queue == "onpage":
        pages = [p for p in pages if p["seo_eligible"]]
    return {**context, "workbench": str(folder), "registration": role, "pages": pages}


def record(folder: Path, revision_id: str, page_id: str, track: str, status: str, event_id: str,
           evidence: str, summary: str, occurred_at: str | None = None) -> Row:
    folder = directory(folder)
    event = event_payload(track, status, event_id, evidence, summary, occurred_at, revision_id)

    def update(page: Row) -> bool:
        return append_event(page, event, timestamp_supplied=occurred_at is not None)

    data, changed = mutate_handoff(folder, revision_id, page_id, update)
    page = next(p for p in records(data, "pages") if p["id"] == page_id)
    return {"ok": True, "recorded": changed, "revision_id": text(data, "revision_id"), "page_id": page_id,
            "track": track, "event_id": event_id, "status": status, "change_number": data["change_number"],
            "progress": obj(page["progress"])[track]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    locate = commands.add_parser("resolve", help="Read the selected workbench, including user choices and actual work")
    target = locate.add_mutually_exclusive_group()
    target.add_argument("--workbench", type=Path, help="Pin this exact local revision")
    target.add_argument("--project-root", type=Path, help="Select the workbench from this site's AGENTS.md")
    locate.add_argument("--site-url")
    locate.add_argument("--market")
    locate.add_argument("--language")
    locate.add_argument("--mode", choices=("real", "simulation"))
    locate.add_argument("--page-id")
    locate.add_argument("--queue", choices=("build", "onpage"))
    report = commands.add_parser("record", help="Record one verified result with a stable retry ID")
    report.add_argument("--workbench", type=Path, required=True)
    for arg in ("revision-id", "page-id", "status", "event-id", "evidence", "summary"):
        report.add_argument("--" + arg, required=True)
    report.add_argument("--track", choices=TRACKS, required=True)
    report.add_argument("--occurred-at", help="ISO 8601 with UTC offset; preserve the original time on delayed delivery")
    args = parser.parse_args()
    try:
        if args.command == "resolve":
            result = resolve(workbench=args.workbench, project_root=args.project_root, site_url=args.site_url,
                             market=args.market, language=args.language, mode=args.mode,
                             page_id=args.page_id, queue=args.queue)
        else:
            result = record(args.workbench, args.revision_id, args.page_id, args.track, args.status,
                            args.event_id, args.evidence, args.summary, args.occurred_at)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
