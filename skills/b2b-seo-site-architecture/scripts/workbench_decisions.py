"""The user's disposition of a skipped page's queries, independent of the research assignment."""
from __future__ import annotations

from plan_records import Row, obj, require, strings, text

ACTIONS = {"pending", "merge", "defer", "out_of_scope"}


def pending_resolution() -> Row:
    return {"action": "pending", "target_page_id": "", "target_module_id": "", "reason": "", "keyword_ids": []}


def initialize_choices(page: Row) -> None:
    page.setdefault("business_answer", "")
    page.setdefault("demand_resolution", pending_resolution())
    text(page, "business_answer", empty=True)
    saved = obj(page["demand_resolution"])
    require(text(saved, "action") in ACTIONS, "Unknown demand resolution")
    strings(saved, "keyword_ids")
    for field in ("target_page_id", "target_module_id", "reason"):
        text(saved, field, empty=True)
    for field in ("business_version", "demand_version"):
        version = page.setdefault(field, 0)
        require(type(version) is int and version >= 0, f"Invalid {field}")


def user_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} requires text")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    require(len(value) <= 1600 and all(c in "\n\t" or c.isprintable() for c in value),
            f"{field} must contain at most 1600 characters")
    return value


def proposed_resolution(value: object, source: Row, pages: dict[str, Row]) -> Row:
    request = obj(value)
    require(set(request) <= {"action", "target_page_id", "target_module_id", "reason"}, "Unknown resolution field")
    action = text(request, "action")
    require(action in ACTIONS, "Unknown demand resolution")
    require(source["decision"] == "skip", "Only a skipped page can redirect its assigned queries")
    members = strings(source, "assigned_keyword_ids")
    require(bool(members), "This page has no assigned queries")
    target = request.get("target_page_id", "")
    module = request.get("target_module_id", "")
    if action == "merge":
        if not isinstance(target, str) or target not in pages or target == source["id"]:
            raise ValueError("Choose another planned page")
        page = pages[target]
        require(page["decision"] != "skip" and module in strings(page, "module_ids"),
                "Choose an available page and one of its planned sections")
    else:
        require(target == "" and module == "", "A target section belongs only to a merge")
    return {"action": action, "target_page_id": target, "target_module_id": module,
            "reason": user_text(request.get("reason", ""), "reason"), "keyword_ids": members if action != "pending" else []}
