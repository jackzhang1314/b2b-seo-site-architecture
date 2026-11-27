"""Commercial decisions and research leads that may not yet own a keyword or URL."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from urllib.parse import urlsplit

from plan_records import Row, index, obj, optional_records, records, require, strings, text

STATUSES = {"pending", "research", "page", "section", "filter", "deferred", "excluded"}
FIELDS = ["id", "title", "status", "question", "reason", "page_id", "urls", "keyword_ids",
          "offering_ids", "evidence_ids", "traffic_refs", "market", "language"]
LINK_FIELDS = ("urls", "keyword_ids", "offering_ids", "evidence_ids")


def build_opportunities(plan: Row, words: dict[str, Row], traffic: list[dict[str, str]],
                        rankings: list[Row], comparisons: list[Row]) -> list[Row]:
    project = obj(plan["project"])
    market, language = text(project, "market"), text(project, "language")
    pages, offers, sources = (index(records(plan, key)) for key in ("pages", "offerings", "sources"))
    targets = {text(t, "url"): t for t in optional_records(plan, "research_targets")}
    observations: dict[str, list[str]] = defaultdict(list)
    for row in traffic:
        if (row["market"], row["language"]) == (market, language):
            observations[row["url"]].append(row["source_ref"])
    ranked = {str(r["url"]) for r in rankings if (r.get("market"), r.get("language")) == (market, language)
              and (r.get("keyword_id") or r.get("keyword_original")) and r.get("url")}
    rows: list[Row] = []
    covered: dict[str, set[str]] = {key: set() for key in (*LINK_FIELDS, "page_id")}
    identifiers: set[str] = set()

    def add(item: Row) -> None:
        ident, status = text(item, "id"), text(item, "status")
        require(ident not in identifiers and status in STATUSES, f"Duplicate opportunity or invalid status: {ident}")
        identifiers.add(ident)
        title, reason = text(item, "title"), text(item, "reason")
        question = text(item, "question") if status in {"pending", "research"} else str(item.get("question", ""))
        page = text(item, "page_id", empty=True) if "page_id" in item else ""
        require(not page or page in pages, f"Unknown opportunity page: {ident}/{page}")
        require(bool(page) or status not in {"page", "section", "filter"}, f"Missing opportunity target page: {ident}")
        links = {key: strings(item, key) if key in item else [] for key in LINK_FIELDS}
        require(bool(page or any(links.values())), f"Opportunity needs a source or linked page: {ident}")
        for url in links["urls"]:
            parsed = urlsplit(url)
            require(parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username
                    and not parsed.password, f"Invalid opportunity source URL: {ident}")
        scoped = all(k in words and (words[k]["market"], words[k]["language"]) == (market, language)
                     for k in links["keyword_ids"])
        require(scoped and set(links["offering_ids"]) <= offers.keys() and set(links["evidence_ids"]) <= sources.keys(),
                f"Unknown or out-of-scope opportunity reference: {ident}")
        for key, values in links.items():
            covered[key].update(values)
        if page:
            covered["page_id"].add(page)
        rows.append({"id": ident, "title": title, "status": status, "question": question, "reason": reason,
                     "page_id": page, **links, "traffic_refs": [ref for u in links["urls"] for ref in observations[u]],
                     "market": market, "language": language})

    for decision in optional_records(plan, "page_opportunities"):
        require(not text(decision, "id").startswith("auto/"), "auto/ is reserved for derived opportunities")
        add(decision)
    for pid, page in pages.items():
        if page.get("state") == "conditional" and pid not in covered["page_id"]:
            add({"id": "auto/page/" + pid, "title": page["name"], "status": "pending", "page_id": pid,
                 "question": "采用这个页面前，需要核实哪些业务条件？", "reason": page["rationale"],
                 "offering_ids": page.get("offering_ids", []), "evidence_ids": page.get("evidence_ids", [])})
    for oid, offer in offers.items():
        if offer["status"] == "unknown" and oid not in covered["offering_ids"]:
            add({"id": "auto/offer/" + oid, "title": offer["label"], "status": "pending", "offering_ids": [oid],
                 "question": f"本企业能否提供 {offer['label']}？可提供哪些产品或服务资料？",
                 "reason": "企业供货范围尚待核实。", "evidence_ids": offer.get("evidence_ids", [])})
    groups: dict[str, list[str]] = defaultdict(list)
    for kid, word in words.items():
        if (word["market"], word["language"]) == (market, language) and word["disposition"] == "defer" and kid not in covered["keyword_ids"]:
            groups[text(word, "group_id")].append(kid)
    for gid, members in groups.items():
        first = words[members[0]]
        add({"id": "auto/group/" + gid, "title": first["buyer_task"], "status": "research", "keyword_ids": members,
             "question": "这些尚待核实的采购需求适合由哪个页面回答？",
             "reason": " / ".join(dict.fromkeys(str(words[k]["reason"]) for k in members)),
             "urls": list(dict.fromkeys(u for k in members for u in strings(words[k], "ranked_urls")))})
    linked = {str(c["url"]) for c in comparisons}
    for url in sorted(observations.keys() - ranked - linked - covered["urls"]):
        target = targets.get(url, {})
        if project.get("exclude_blog") and target.get("page_type") in {"article", "blog", "blog_category"}:
            continue
        ident = sha256(url.encode()).hexdigest()[:24]
        add({"id": "auto/url/" + ident, "title": str(target.get("label") or urlsplit(url).path or url),
             "status": "research", "question": "同行这一页解决什么采购问题，本企业是否也服务这项需求？",
             "reason": target.get("reason") or "Top Pages 收录了这个网址，本市场还没有对应的逐词排名明细。",
             "urls": [url]})
    return rows
