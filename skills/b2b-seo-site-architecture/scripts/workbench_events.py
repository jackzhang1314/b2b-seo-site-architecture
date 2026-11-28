"""Derive page progress from timestamped, retry-safe reports by the executing tools."""
from __future__ import annotations

from datetime import datetime, timezone

from plan_records import Row, obj, records, strings, text
from workbench_decisions import initialize_choices

SCHEMA = "b2b-seo-workbench-handoff/1"
TRACKS = {"build": ("built", "published"), "onpage": ("optimized", "published", "reviewed")}
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
        raise ValueError("build supports built / published; onpage supports optimized / published / reviewed")
    return {"track": track, "status": status, "event_id": event_text(event_id, "event_id", 256),
            "evidence": event_text(evidence, "evidence", 2048), "summary": event_text(summary, "summary", 1200),
            "occurred_at": timestamp(occurred_at) if occurred_at is not None else utc_now(),
            "origin_revision_id": revision_id}


def append_event(page: Row, event: Row, *, timestamp_supplied: bool) -> bool:
    history = records(page, "history")
    for old in history:
        if (old["origin_revision_id"], old["track"], old["event_id"]) != (
                event["origin_revision_id"], event["track"], event["event_id"]):
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


def alignment_origins(page: Row, revision: str) -> list[str]:
    """Recover equivalent revisions for 0.9 plans whose receipts predate explicit scope tracking."""
    if "onpage_scope_revisions" in page:
        origins = strings(page, "onpage_scope_revisions", required=True)
        if revision not in origins:
            raise ValueError("This page's SEO scope does not include its own revision")
        return origins
    origins = [revision]
    review = obj(page.get("revision_review", {}))
    if review.get("status") == "unchanged":
        origins.extend(text(e, "origin_revision_id") for e in records(page, "history"))
        origins.append(text(review, "source_revision_id"))
    elif review.get("status") == "changed":
        changed_in = text(review, "changed_in")
        origins.append(changed_in)
        if changed_in != revision:
            origins.append(text(review, "source_revision_id"))
    return list(dict.fromkeys(origins))


def invalidate_alignment(page: Row, at: str, revision_id: str) -> None:
    """Existing content must cover new user-confirmed assignments or facts before it is aligned again."""
    page["onpage_scope_revisions"] = [revision_id]
    page["onpage_review_after"] = timestamp(at)


def derive_progress(page: Row) -> Row:
    by_id: dict[tuple[str, str, str], Row] = {}
    for raw in records(page, "history"):
        track, status, event_id = text(raw, "track"), text(raw, "status"), text(raw, "event_id")
        checked = event_payload(track, status, event_id, text(raw, "evidence"), text(raw, "summary"),
                                text(raw, "occurred_at"), text(raw, "origin_revision_id"))
        key = (text(checked, "origin_revision_id"), track, text(checked, "event_id"))
        if key in by_id and by_id[key] != checked:
            raise ValueError("Conflicting receipts for the same page, track and event ID")
        by_id[key] = checked
    history = list(by_id.values())
    # A delayed callback must not roll the current status back. Publication wins a tie.
    history.sort(key=lambda e: (text(e, "occurred_at"), e.get("status") == "published", text(e, "event_id"),
                                text(e, "origin_revision_id")))
    progress: Row = {}
    for track in TRACKS:
        latest: Row = {"status": "not_started", "last_published_at": None}
        for event in history:
            if event.get("track") != track or event["status"] == "reviewed":
                continue
            latest.update(event)
            if event["status"] == "published":
                latest["last_published_at"] = event["occurred_at"]
        progress[track] = latest
    page["history"] = history
    page["progress"] = progress
    origins = set(strings(page, "onpage_scope_revisions", required=True))
    after = timestamp(text(page, "onpage_review_after")) if page.get("onpage_review_after") else ""
    checks = [e for e in history if e["track"] == "onpage"]
    proof = next((e for e in reversed(checks) if e["origin_revision_id"] in origins
                  and text(e, "occurred_at") >= after), None)
    # A later publication from an incompatible plan can change the live copy after it was checked.
    if proof and any(e["status"] == "published" and e["origin_revision_id"] not in origins
                     and text(e, "occurred_at") > text(proof, "occurred_at") for e in checks):
        proof = None
    page["onpage_alignment"] = "aligned" if proof else "needs_review" if checks else "not_reviewed"
    page["onpage_alignment_receipt"] = proof
    exists = page.get("page_action") in ("reuse", "revise") or obj(progress["build"])["status"] != "not_started"
    page["seo_eligible"] = page["seo_decision"] == "requested" and (page.get("decision") == "build" or exists)
    return page


def normalize_handoff(data: Row) -> Row:
    """Accept 0.8 handoffs, retain their choices and expose the current additive contract."""
    if data.get("schema") != SCHEMA:
        raise ValueError("Unsupported workbench handoff")
    revision = text(data, "revision_id")
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
        initialize_choices(page)
        page["onpage_scope_revisions"] = alignment_origins(page, revision)
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
               alignment="onpage_alignment 区分当前页面范围已核对、需要复核与尚无核对记录。复核现有内容已覆盖时写 reviewed 回执。",
               progress="按页读取 build 与 onpage 两组工作回执。暂无回执表示工作台尚未取得实际工作证据。",
               writeback="执行工具回读成果后，以本版 revision_id 和页号调用架构 Skill 的 workbench_progress.py record。")
    data["use"] = use
    return data
