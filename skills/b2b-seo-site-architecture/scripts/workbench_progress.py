"""Resolve a website's page work queue or record verified work in its selected revision."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from page_confirmation import FILENAME, mutate_handoff, mutate_locked, read_handoff
from plan_records import Row, index, obj, records, text
from register_project import project_lock, resolve_workbench, sync_entry_locked
from revisions import read_revisions
from seo_project import INDEX, identity, local_file, project_for
from workbench_events import TRACKS, append_event, event_payload, normalize_handoff
from workbench_update import page_key, project_scope


def directory(path: Path) -> Path:
    folder = path.resolve()
    if folder.is_file() and folder.name in {FILENAME, "index.html", "design.html", "seo-project-index.json"}:
        folder = folder.parent
    if not folder.is_dir():
        raise ValueError("Specify a local workbench directory")
    return folder


def refresh_receipts_locked(root: Path, adopted: Path, page_id: str | None = None) -> Row:
    """Reconcile original evidence along explicit stable page lineage while holding the project lock."""
    with project_lock(adopted):
        active = read_handoff(adopted)
        folder, data = adopted, active
        pages = index(records(active, "pages"))
        eligible = {pid for pid in pages if page_id is None or pid == page_id}
        known = {pid: {(e["origin_revision_id"], e["track"], e["event_id"]): e
                       for e in records(pages[pid], "history")} for pid in eligible}
        pending: dict[str, list[Row]] = {}
        visited = {folder}
        scope = project_scope(obj(active["project"]))
        while eligible and data.get("update_from"):
            link = obj(data["update_from"])
            path = Path(text(link, "directory"))
            parent = (folder / path).resolve()
            if (path.is_absolute() or not parent.is_relative_to(root) or parent in visited
                    or not (parent / FILENAME).is_file() or project_for(parent) != root):
                break
            # Only receipts and page identity are needed here, including for pre-0.10 revisions.
            prior = normalize_handoff(obj(json.loads(local_file(parent, FILENAME).read_bytes())))
            if prior["revision_id"] != link["revision_id"] or project_scope(obj(prior["project"])) != scope:
                break
            previous = index(records(prior, "pages"))
            stable: set[str] = set()
            for pid in eligible & previous.keys():
                review = obj(pages[pid].get("revision_review", {}))
                if (review.get("status") not in {"unchanged", "changed"}
                        or review.get("source_revision_id") != prior["revision_id"]
                        or page_key(pages[pid]) != page_key(previous[pid])):
                    continue
                stable.add(pid)
                for e in records(previous[pid], "history"):
                    if e["origin_revision_id"] != prior["revision_id"]:
                        continue
                    key = (e["origin_revision_id"], e["track"], e["event_id"])
                    if known[pid].get(key) != e:
                        pending.setdefault(pid, []).append(e)
            eligible, pages, folder, data = stable, previous, parent, prior
            visited.add(folder)
        for pid, events in pending.items():
            def copy_events(page: Row) -> bool:
                changed = False
                for event in events:
                    if append_event(page, event, timestamp_supplied=True):
                        changed = True
                return changed

            active, _ = mutate_locked(adopted, root, text(active, "revision_id"), pid, copy_events)
        return active


def project_late_receipt(folder: Path, page_id: str, data: Row) -> None:
    root = project_for(folder)
    if root is None or root == folder or not (root / "AGENTS.md").is_file():
        return
    with project_lock(root):
        entries = read_revisions(root, local_file(root, "AGENTS.md").read_text(encoding="utf-8-sig"))
        source = entries.get(folder / INDEX)
        if source is None or source[0] == "adopted":
            return
        scope = project_scope(obj(data["project"]))
        for key, (role, entry) in entries.items():
            if role == "adopted" and project_scope(entry) == scope and (key.parent / FILENAME).is_file():
                refresh_receipts_locked(root, key.parent, page_id)
                break


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
        if role == "adopted" and root != folder:
            context = refresh_receipts_locked(root, folder, page_id)
        else:
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
    # Retrying an already-saved event also completes a projection interrupted after the source write.
    project_late_receipt(folder, page_id, data)
    page = next(p for p in records(data, "pages") if p["id"] == page_id)
    return {"ok": True, "recorded": changed, "revision_id": text(data, "revision_id"), "page_id": page_id,
            "track": track, "event_id": event_id, "status": status, "change_number": data["change_number"],
            "progress": obj(page["progress"])[track], "onpage_alignment": page["onpage_alignment"]}


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
