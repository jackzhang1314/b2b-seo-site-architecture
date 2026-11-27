"""Derive page progress from timestamped, retry-safe reports by the executing tools."""
from __future__ import annotations

from datetime import datetime, timezone

from plan_records import Row, obj, records, text

SCHEMA = "b2b-seo-workbench-handoff/1"
TRACKS = {"build": ("built", "published"), "onpage": ("optimized", "published")}
DECISIONS = ("pending", "build", "skip")
SEO_CHOICES = ("hold", "requested")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def timestamp(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("Missing UTC offset")
        return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (OverflowError, ValueError) as exc:
        raise ValueError("occurred_at requires an ISO 8601 timestamp with a UTC offset") from exc


def event_text(value: str, field: str, limit: int) -> str:
    result = value.strip()
    if not result or len(result) > limit or not result.isprintable():
        raise ValueError(f"{field} requires one line of 1–{limit} printable characters")
    return result


def event_payload(track: str, status: str, event_id: str, evidence: str, summary: str,
                  occurred_at: str | None, revision_id: str) -> Row:
    if track not in TRACKS or status not in TRACKS[track]:
        raise ValueError("build supports built / published; onpage supports optimized / published")
    return {"track": track, "status": status, "event_id": event_text(event_id, "event_id", 256),
            "evidence": event_text(evidence, "evidence", 2048), "summary": event_text(summary, "summary", 1200),
            "occurred_at": timestamp(occurred_at) if occurred_at is not None else utc_now(),
            "origin_revision_id": revision_id}


def append_event(page: Row, event: Row, *, timestamp_supplied: bool) -> bool:
    history = records(page, "history")
    for old in history:
        if (old["track"], old["event_id"]) != (event["track"], event["event_id"]):
            continue
        comparison = ["status", "evidence", "summary"]
        if timestamp_supplied:
            comparison.append("occurred_at")
        if any(old[k] != event[k] for k in comparison):
            raise ValueError("This page and track already have an event with this ID and different contents")
        return False
    history.append(event)
    page["history"] = history
    return True


def derive_progress(page: Row) -> Row:
    by_id: dict[tuple[str, str], Row] = {}
    for raw in records(page, "history"):
        track, status, event_id = text(raw, "track"), text(raw, "status"), text(raw, "event_id")
        checked = event_payload(track, status, event_id, text(raw, "evidence"), text(raw, "summary"),
                                text(raw, "occurred_at"), text(raw, "origin_revision_id"))
        key = (track, text(checked, "event_id"))
        if key in by_id and by_id[key] != checked:
            raise ValueError("Conflicting receipts for the same page, track and event ID")
        by_id[key] = checked
    history = list(by_id.values())
    # A delayed callback must not roll the current status back. Publication wins a tie.
    history.sort(key=lambda e: (text(e, "occurred_at"), e.get("status") == "published", text(e, "event_id")))
    progress: Row = {}
    for track in TRACKS:
        latest: Row = {"status": "not_started", "last_published_at": None}
        for event in history:
            if event.get("track") != track:
                continue
            latest.update(event)
            if event["status"] == "published":
                latest["last_published_at"] = event["occurred_at"]
        progress[track] = latest
    page["history"] = history
    page["progress"] = progress
    exists = page.get("page_action") in ("reuse", "revise") or obj(progress["build"])["status"] != "not_started"
    page["seo_eligible"] = page["seo_decision"] == "requested" and (page.get("decision") == "build" or exists)
    return page


def normalize_handoff(data: Row) -> Row:
    """Accept 0.8 handoffs, retain their choices and expose the current additive contract."""
    if data.get("schema") != SCHEMA:
        raise ValueError("Unsupported workbench handoff")
    text(data, "revision_id")
    pages = records(data, "pages")
    ids = set()
    for page in pages:
        page_id = text(page, "id")
        if page_id in ids:
            raise ValueError("Duplicate page in handoff")
        ids.add(page_id)
        page.setdefault("seo_decision", "hold")
        page.setdefault("seo_version", 0)
        page.setdefault("seo_decided_at", None)
        page.setdefault("history", [])
        page.setdefault("choice_history", [])
        if page.get("decision") not in DECISIONS or page["seo_decision"] not in SEO_CHOICES:
            raise ValueError("Unknown user choice")
        for key in ("decision_version", "seo_version"):
            version = page.get(key)
            if type(version) is not int or version < 0:
                raise ValueError(f"Invalid {key}")
        derive_progress(page)
    data["pages"] = pages
    data["workflow_version"] = 1
    use = obj(data.get("use", {}))
    use.update(build="建页仅处理 decision=build。跳过与待定保留现有网站内容。",
               seo="seo_decision 是独立排期。seo_eligible=true 才进入本轮优化队列。",
               progress="按页读取 build 与 onpage 两组工作回执。暂无回执表示工作台尚未取得实际工作证据。",
               writeback="执行工具回读成果后，以本版 revision_id 和页号调用架构 Skill 的 workbench_progress.py record。")
    data["use"] = use
    return data
