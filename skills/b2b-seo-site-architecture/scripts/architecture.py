#!/usr/bin/env python3
"""Validate design architecture, separate article plans, and render a design handoff."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from delivery import collect_analysis

# The patch release retains the 0.4.0 plan schema.
VERSION = "0.4.0"
TOOL_VERSION = "0.4.1"
DESIGN_VERSIONS = {"0.3.0", VERSION}
SUPPORTED_VERSIONS = {"0.1.0", "0.2.0", *DESIGN_VERSIONS}
BLOG_TYPES = {"blog", "blog_category", "article"}
PAGE_TYPES = {
    "home", "category_overview", "category", "product", "attachment", "application_hub", "application",
    "capability", "company", "resource", "utility", "inquiry", *BLOG_TYPES,
}
SOURCE_KINDS = {
    "user_provided", "provider_export", "browser_observation", "historical_sample", "model_inference", "simulation",
}
TARGET_DISPOSITIONS = {"page", "section", "filter"}
Row = dict[str, object]


def obj(value: object) -> Row:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("Expected an object with string keys")
    return {str(k): v for k, v in value.items()}


def text(row: Row, key: str, *, empty: bool = False) -> str:
    value = row.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"Expected {'possibly empty ' if empty else ''}string: {key}")
    return value


def strings(row: Row, key: str, *, required: bool = False, unique: bool = True) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"Expected string list: {key}")
    result = [str(v) for v in value]
    if required and not result:
        raise ValueError(f"Expected nonempty list: {key}")
    if unique and len(result) != len(set(result)):
        raise ValueError(f"Duplicate values in {key}")
    return result


def records(row: Row, key: str) -> list[Row]:
    value = row.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Expected object list: {key}")
    return [obj(v) for v in value]


def optional_records(row: Row, key: str) -> list[Row]:
    return records(row, key) if key in row else []


def traffic_estimate(row: Row) -> float | None:
    value = row.get("estimated_traffic")
    if value is None or isinstance(value, str) and value.strip().casefold() in {"", "n/a", "na", "unknown", "missing"}:
        return None
    require(isinstance(value, (str, int, float)) and not isinstance(value, bool), "Invalid traffic estimate")
    result = float(str(value))
    require(math.isfinite(result) and result >= 0, "Invalid traffic estimate")
    return result


def index(rows: list[Row]) -> dict[str, Row]:
    result: dict[str, Row] = {}
    for row in rows:
        ident = text(row, "id")
        if ident in result:
            raise ValueError(f"Duplicate id: {ident}")
        result[ident] = row
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def http_url(value: str) -> str:
    parsed = urlsplit(value)
    require(parsed.scheme in {"http", "https"} and bool(parsed.hostname), f"Invalid HTTP URL: {value}")
    require(not parsed.username and not parsed.password, "Credentials in URL are not supported")
    _ = parsed.port
    return str(parsed.hostname).lower()


def read_keywords(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        required = {"keyword_id", "keyword_original", "keyword_normalized", "market", "language"}
        require(required <= set(fields) and len(fields) == len(set(fields)), "Missing or duplicate keyword columns")
        result: dict[str, dict[str, str]] = {}
        keys: set[tuple[str, str, str]] = set()
        for row in reader:
            require(None not in row and all(v is not None for v in row.values()), "Unequal keyword CSV fields")
            clean = {str(k): str(v) for k, v in row.items()}
            ident = clean["keyword_id"]
            normalized = " ".join(unicodedata.normalize("NFC", clean["keyword_original"]).casefold().split())
            require(bool(ident) and ident not in result and bool(normalized), "Duplicate/empty keyword id or text")
            require(clean["keyword_normalized"] == normalized, "Keyword normalization differs from source contract")
            key = (normalized, clean["market"], clean["language"])
            require(key not in keys, "Duplicate normalized keyword context")
            keys.add(key)
            result[ident] = clean
    require(bool(result), "Empty keyword input")
    return result


def check_tree(rows: dict[str, Row], label: str) -> None:
    for ident in rows:
        visited: set[str] = set()
        current = ident
        while current:
            require(current in rows, f"Unknown {label} parent: {current}")
            require(current not in visited, f"Cycle in {label}: {ident}")
            visited.add(current)
            current = text(rows[current], "parent_id", empty=True)


def design_research_coverage(plan: Row) -> Row:
    """Count the latest recorded round for every declared core-page target."""
    targets = optional_records(plan, "research_targets")
    latest: dict[tuple[str, str], Row] = {}
    for review in optional_records(plan, "competitor_page_reviews"):
        key = (text(review, "competitor_id"), text(review, "url"))
        if key not in latest or int(str(review.get("review_round", 0))) > int(str(latest[key].get("review_round", 0))):
            latest[key] = review
    rows: list[Row] = []
    for target in targets:
        review = latest.get((text(target, "competitor_id"), text(target, "url")), {})
        rows.append({**target, "latest_status": review.get("status", "not_started"),
                     "latest_review_id": review.get("id", ""), "latest_round": review.get("review_round", 0)})
    counts = Counter(text(row, "latest_status") for row in rows)
    return {"target_count": len(rows), "latest_status_counts": dict(counts), "targets": rows,
            "complete": bool(rows) and counts["observed"] == len(rows)}


def validate_design_details(plan: Row, pages: dict[str, Row], templates: dict[str, Row],
                            sources: dict[str, Row], competitors: dict[str, Row]) -> Row:
    if plan.get("schema_version") not in DESIGN_VERSIONS:
        return {"status": "legacy_not_provided", "complete": False}

    def evidence(row: Row, *, required: bool = True) -> None:
        ids = strings(row, "evidence_ids", required=required)
        require(set(ids) <= sources.keys(), "Unknown design evidence")
        if obj(plan["project"]).get("mode") == "real":
            require(all(sources[s].get("kind") != "simulation" for s in ids),
                    "Simulated evidence cannot support a real design specification")

    def fields(rows: list[Row]) -> dict[str, Row]:
        mapped = index(rows)
        require(bool(mapped), "Design field definitions are required")
        for field in mapped.values():
            for key in ("label", "format", "source", "missing_policy"):
                text(field, key)
            require(field.get("kind") in {"text", "long_text", "email", "phone", "rich_text", "image", "gallery", "number", "boolean",
                                          "choice", "link", "file", "reference", "repeater"}, "Unknown field kind")
            require(field.get("scope") in {"site", "page", "item"}, "Unknown field scope")
            require(isinstance(field.get("required"), bool), "Explicit field requirement needed")
            require(field.get("value_status") in {"available", "missing", "visitor_input", "not_applicable"}, "Unknown field value status")
            evidence(field, required=field.get("value_status") == "available")
        return mapped

    site = obj(plan.get("site_design"))
    for key in ("visual_direction", "typography", "spacing", "image_style", "header", "footer", "mobile_navigation"):
        text(site, key)
    evidence(site)
    shared_fields = fields(records(site, "fields"))
    factory = obj(plan.get("factory_profile"))
    require(factory.get("business_role") in {"manufacturer", "trading_company", "mixed", "unknown"},
            "Explicit business role required; do not infer factory identity")
    text(factory, "identity_statement")
    evidence(factory)
    proofs = index(records(factory, "proof_items"))
    for proof in proofs.values():
        for key in ("topic", "claim", "scope", "presentation", "missing_action"):
            text(proof, key)
        status = text(proof, "status")
        require(status in {"verified", "pending", "simulation", "not_applicable"}, "Unknown factory proof status")
        evidence(proof, required=status in {"verified", "simulation"})
        ids = strings(proof, "evidence_ids")
        if status == "verified":
            require(any(sources[s].get("kind") == "user_provided" for s in ids)
                    and all(sources[s].get("kind") != "simulation" for s in ids),
                    "Verified factory claims require own-company documents, not competitor or simulation evidence")
        if status == "simulation":
            require(obj(plan["project"]).get("mode") == "simulation"
                    and any(sources[s].get("kind") == "simulation" for s in ids), "Mock factory proof must remain simulated")
        if proof.get("topic") == "certification" and status in {"verified", "simulation"}:
            for key in ("issuer", "validity", "certificate_scope"):
                text(proof, key)
    mapped_pages: set[str] = set()
    for item in records(factory, "page_proof_map"):
        pid = text(item, "page_id")
        require(pid in pages and pid not in mapped_pages, "Unknown or duplicate factory page mapping")
        mapped_pages.add(pid)
        require(set(strings(item, "proof_ids", required=True)) <= proofs.keys(), "Unknown factory proof reference")
        for key in ("presentation", "reason"):
            text(item, key)
    factory_pages = {pid for pid, p in pages.items() if p.get("page_type") in
                     {"home", "company", "category", "category_overview", "product", "capability", "inquiry"}}
    require(factory_pages <= mapped_pages, "Core commercial pages need role-appropriate factory/trust planning")
    targets = index(records(plan, "research_targets"))
    target_keys: set[tuple[str, str]] = set()
    for target in targets.values():
        cid = text(target, "competitor_id")
        require(cid in competitors, "Unknown research target competitor")
        host = http_url(text(target, "url"))
        domain = text(competitors[cid], "domain").lower()
        require(host == domain or host.endswith("." + domain), "Research target outside competitor domain")
        target_key = (cid, text(target, "url"))
        require(target_key not in target_keys, "Duplicate research target")
        target_keys.add(target_key)
        for name in ("page_type", "reason"):
            text(target, name)
    reviews = index(records(plan, "competitor_page_reviews"))
    rounds: set[tuple[str, str, int]] = set()
    for review in reviews.values():
        review_key = (text(review, "competitor_id"), text(review, "url"))
        require(review_key in target_keys, "Page review has no declared research target")
        round_number = review.get("review_round")
        require(isinstance(round_number, int) and not isinstance(round_number, bool) and round_number > 0,
                "Review round must be positive")
        stamp = (review_key[0], review_key[1], int(str(round_number)))
        require(stamp not in rounds, "Duplicate page review round")
        rounds.add(stamp)
        text(review, "observed_at")
        text(review, "follow_up", empty=True)
        if review.get("status") in {"observed", "partial", "simulated"}:
            for name in ("content_summary", "desktop_notes", "mobile_notes"):
                text(review, name)
            details = records(review, "module_details")
            require([text(m, "name") for m in details] == strings(review, "modules", required=True),
                    "Research module order differs from summary")
            for module in details:
                for name in ("content", "layout", "interaction", "assessment"):
                    text(module, name)
                strings(module, "fields", required=True)
        else:
            require(not optional_records(review, "module_details") and not strings(review, "modules"),
                    "Unvisited pages cannot contain observed modules")
    module_count = field_count = 0
    for template in templates.values():
        spec = obj(template.get("design_spec"))
        local_fields = fields(records(spec, "fields"))
        require(not (local_fields.keys() & shared_fields.keys()), "Local and shared design field ids collide")
        modules = index(records(spec, "modules"))
        require(bool(modules), "Design module specifications are required")
        require([text(m, "name") for m in modules.values()] == strings(template, "modules"),
                "Design module order differs from template summary")
        require([text(f, "label") for f in local_fields.values()] == strings(template, "fields"),
                "Design field labels differ from template summary")
        strings(spec, "research_gaps")
        strings(spec, "rejected_patterns")
        used_fields: set[str] = set()
        for module in modules.values():
            for name in ("name", "purpose", "desktop_layout", "mobile_layout", "interaction", "condition", "decision"):
                text(module, name)
            strings(module, "content", required=True)
            require(isinstance(module.get("required"), bool), "Explicit module requirement needed")
            require(set(strings(module, "variants")) <= set(strings(template, "variants")), "Unknown module variant")
            field_ids = strings(module, "field_ids", required=True)
            require(set(field_ids) <= (local_fields.keys() | shared_fields.keys()),
                    f"templates[{template['id']}].design_spec.modules[{module['id']}].field_ids: "
                    "Unknown module field reference; IDs must exist in this template or site_design.fields")
            used_fields.update(field_ids)
            ids = strings(module, "study_ids")
            require(set(ids) <= reviews.keys(), "Unknown module study reference")
            require(all(reviews[s].get("status") in {"observed", "partial", "simulated"} for s in ids),
                    "Unvisited page cannot support a module decision")
            evidence(module)
        require(local_fields.keys() <= used_fields, "Unused design field definition")
        for variant in strings(template, "variants"):
            require(any(not strings(m, "variants") or variant in strings(m, "variants") for m in modules.values()),
                    "Template variant has no design modules")
        module_count += len(modules)
        field_count += len(local_fields)
    required_pages = {pid for pid, p in pages.items() if p.get("page_type") != "article"}
    required_pages.update(pid for t in templates.values() for pid in strings(t, "representative_page_ids"))
    for pid, page in pages.items():
        if pid not in required_pages and "design_brief" not in page:
            continue
        brief = obj(page.get("design_brief"))
        for name in ("audience", "primary_message", "copy_direction", "image_direction", "emphasis"):
            text(brief, name)
        evidence(brief)
        spec = obj(templates[text(page, "template_id")]["design_spec"])
        modules = index(records(spec, "modules"))
        noted: set[str] = set()
        for note in records(brief, "module_notes"):
            mid = text(note, "module_id")
            require(mid in modules and mid not in noted, "Unknown or duplicate page module note")
            variants = strings(modules[mid], "variants")
            require(not variants or page["variant"] in variants, "Page note targets an inactive module variant")
            text(note, "content_note")
            noted.add(mid)
    coverage = design_research_coverage(plan)
    return {"status": "specified", "templates": len(templates), "modules": module_count,
            "fields": field_count, "shared_fields": len(shared_fields), "required_page_briefs": len(required_pages),
            "review_records": len(reviews), "research": coverage,
            "factory_proofs": len(proofs), "factory_page_mappings": len(mapped_pages),
            "complete": coverage["complete"]}


def validate(plan: Row, keywords: dict[str, dict[str, str]]) -> Row:
    require(plan.get("schema_version") in SUPPORTED_VERSIONS, "Unsupported architecture schema")
    project = obj(plan.get("project"))
    for key in ("name", "site_url", "market", "language", "scope_note"):
        text(project, key)
    http_url(text(project, "site_url"))
    require(urlsplit(text(project, "site_url")).path in {"", "/"}, "Project site_url must be a site root")
    require(project.get("mode") in {"real", "simulation"}, "Explicit real/simulation mode required")
    require(isinstance(project.get("exclude_blog"), bool), "Explicit blog planning scope required")
    if plan.get("schema_version") == "0.1.0":
        require(project.get("exclude_blog") is True, "Legacy schema supports non-blog architecture only")
    sources = index(records(plan, "sources"))
    for source in sources.values():
        require(source.get("kind") in SOURCE_KINDS, "Unknown source kind")
        for key in ("label", "reference", "scope"):
            text(source, key)

    def evidence(row: Row) -> list[str]:
        ids = strings(row, "evidence_ids", required=True)
        require(set(ids) <= sources.keys(), "Unknown evidence reference")
        return ids

    offerings = index(records(plan, "offerings"))
    for offering in offerings.values():
        text(offering, "label")
        require(offering.get("status") in {"supported", "unknown", "not_offered"}, "Invalid offering status")
        evidence(offering)

    competitors = index(records(plan, "competitors"))
    for competitor in competitors.values():
        for key in ("name", "domain", "role", "summary", "coverage"):
            text(competitor, key)
        require(competitor.get("visit_status") in {"observed", "partial", "blocked", "not_started", "simulated"},
                "Invalid competitor visit status")
        require(isinstance(competitor.get("menu_observed"), bool), "Explicit menu observation flag required")
        if "sample_group" in competitor:
            require(competitor.get("sample_group") in {"top_competitor", "low_authority_high_relevant_traffic", "other"},
                    "Unknown competitor sample group")
            text(competitor, "selection_reason")
        source_ids = evidence(competitor)
        if competitor.get("menu_observed") is True:
            require(competitor.get("visit_status") in {"observed", "partial"}, "Unvisited competitor has observed menu")
            require(any(sources[s].get("kind") == "browser_observation" for s in source_ids),
                    "Menu observation requires browser evidence")

    groups = index(records(plan, "groups"))
    for group in groups.values():
        for key in ("label", "buyer_task", "rationale"):
            text(group, key)
    templates = index(records(plan, "templates"))
    pages = index(records(plan, "pages"))
    require(bool(pages), "No pages")
    for template in templates.values():
        for key in ("name", "rationale"):
            text(template, key)
        require(template.get("mode") in {"reusable", "bespoke"}, "Invalid template mode")
        for key in ("allowed_page_types", "modules", "fields", "variants", "representative_page_ids"):
            strings(template, key, required=True)
        strings(template, "optional_modules")
        order = template.get("design_order")
        require(isinstance(order, int) and not isinstance(order, bool) and order > 0, "Invalid template design order")
    urls: set[str] = set()
    roots: list[str] = []
    usage: Counter[str] = Counter()
    for ident, page in pages.items():
        for key in ("name", "name_en", "objective", "cta", "priority", "rationale"):
            text(page, key)
        path = text(page, "path")
        parsed = urlsplit(path)
        require(path.startswith("/") and not path.startswith("//") and not any(
            (parsed.scheme, parsed.netloc, parsed.query, parsed.fragment, "\\" in path)
        ), f"Expected a page path without filters or fragments: {path}")
        require(path not in urls, f"Duplicate page URL: {path}")
        urls.add(path)
        parent = text(page, "parent_id", empty=True)
        if not parent:
            roots.append(ident)
            require(page.get("page_type") == "home" and path == "/", "Root must be home")
        allowed_types = PAGE_TYPES - BLOG_TYPES if project.get("exclude_blog") is True else PAGE_TYPES
        require(page.get("page_type") in allowed_types, "Unsupported page type for selected blog scope")
        require(page.get("state") in {"proposed", "conditional"}, "Invalid page state")
        tid = text(page, "template_id")
        require(tid in templates, f"Unknown template: {tid}")
        require(page.get("page_type") in strings(templates[tid], "allowed_page_types"), "Template/page type mismatch")
        require(text(page, "variant") in strings(templates[tid], "variants"), "Unknown template variant")
        usage[tid] += 1
        for key in ("sections", "unique_content"):
            strings(page, key, required=True)
        strings(page, "fact_gaps")
        eids = evidence(page)
        oids = strings(page, "offering_ids")
        require(set(oids) <= offerings.keys(), "Unknown offering")
        if page.get("state") == "proposed":
            require(all(offerings[o].get("status") == "supported" for o in oids),
                    f"Unsupported offering on proposed page: {ident}")
            if project.get("mode") == "real":
                used_sources = eids + [s for o in oids for s in evidence(offerings[o])]
                require(all(sources[s].get("kind") != "simulation" for s in used_sources),
                        "Simulation cannot support real proposed pages")
        text(page, "primary_keyword_id", empty=True)
    require(len(roots) == 1, "Expected exactly one home root")
    check_tree(pages, "page")
    for page in pages.values():
        parent = text(page, "parent_id", empty=True)
        if parent:
            require(pages[parent].get("page_type") != "article", "Articles cannot parent core pages or other articles")
        if page.get("page_type") == "article":
            require(bool(parent) and pages[parent].get("page_type") in {"blog", "blog_category"},
                    "Article requires a blog or blog-category parent")
        if parent and page.get("state") == "proposed":
            require(pages[parent].get("state") == "proposed", "Proposed child has a conditional parent")
    for tid, template in templates.items():
        require(usage[tid] > 0, f"Unused template: {tid}")
        representatives = strings(template, "representative_page_ids")
        for rep in representatives:
            require(rep in pages and pages[rep].get("template_id") == tid, "Invalid template representative page")
        used_variants = {text(p, "variant") for p in pages.values() if p.get("template_id") == tid}
        represented_variants = {text(pages[rep], "variant") for rep in representatives}
        require(used_variants <= represented_variants, f"Missing used variant representative: {tid}")
        if template.get("mode") == "bespoke":
            require(usage[tid] == 1, "Bespoke template must have exactly one instance")

    navigation = index(records(plan, "navigation"))
    for nav in navigation.values():
        for key in ("label", "rationale"):
            text(nav, key)
        require(nav.get("kind") in {"page", "group"}, "Invalid navigation kind")
        require(nav.get("location") in {"header", "footer", "action"}, "Invalid navigation location")
        order = nav.get("order")
        require(isinstance(order, int) and not isinstance(order, bool) and order >= 0, "Invalid navigation order")
        pid = text(nav, "page_id", empty=True)
        if nav.get("kind") == "page":
            require(pid in pages, "Navigation target missing")
            require(pages[pid].get("page_type") != "article", "Article titles belong in the content plan, not navigation")
            require(pages[pid].get("state") == "proposed", "Conditional page must not enter launch navigation")
        else:
            require(not pid, "Menu group must not pretend to be a page")
            require(any(v.get("parent_id") == nav["id"] for v in navigation.values()), "Empty menu group")
        text(nav, "parent_id", empty=True)
    check_tree(navigation, "navigation")
    for nav in navigation.values():
        parent = text(nav, "parent_id", empty=True)
        if parent:
            require(navigation[parent].get("location") == nav.get("location"), "Menu crosses navigation locations")

    decisions = records(plan, "decisions")
    assigned: dict[str, Row] = {}
    contexts: dict[str, set[tuple[str, str]]] = {gid: set() for gid in groups}
    for decision in decisions:
        kid = text(decision, "keyword_id")
        require(kid in keywords and kid not in assigned, "Unknown or duplicate keyword decision")
        assigned[kid] = decision
        gid = text(decision, "group_id")
        require(gid in groups, "Unknown semantic group")
        keyword = keywords[kid]
        contexts[gid].add((keyword["market"], keyword["language"]))
        disposition = text(decision, "disposition")
        require(disposition in TARGET_DISPOSITIONS | {"defer", "exclude"}, "Invalid keyword disposition")
        pid = text(decision, "page_id", empty=True)
        text(decision, "reason")
        evidence(decision)
        if disposition in TARGET_DISPOSITIONS:
            require(pid in pages, "Keyword target page missing")
            require(keyword["market"] == project["market"], "Keyword market differs or is unknown")
            require(keyword["language"] in {"", project["language"]}, "Known keyword language differs")
            require(keyword.get("review_state") != "exclude", "Excluded keyword assigned to page")
        else:
            require(not pid, "Deferred/excluded keyword cannot have a target page")
    require(assigned.keys() == keywords.keys(), "Unassigned keywords")
    require(all(len(v) == 1 for v in contexts.values()), "Unused or mixed-context semantic group")
    for pid, page in pages.items():
        primary = text(page, "primary_keyword_id", empty=True)
        if primary:
            require(primary in assigned and assigned[primary].get("page_id") == pid
                    and assigned[primary].get("disposition") == "page",
                    f"pages[{pid}].primary_keyword_id {primary!r}: Page primary keyword is not a page-level assignment; "
                    "use a decisions row with disposition=page and the same page_id, or leave primary_keyword_id empty. "
                    "Do not promote a section/filter keyword merely to satisfy this check.")

    rankings = index(records(plan, "ranking_observations"))
    ranking_counts: Counter[str] = Counter()
    for ranking in rankings.values():
        kid, cid, sid = (text(ranking, key) for key in ("keyword_id", "competitor_id", "source_id"))
        require(kid in keywords and cid in competitors and sid in sources, "Unresolved ranking relation")
        host = http_url(text(ranking, "url"))
        domain = text(competitors[cid], "domain").lower()
        require(host == domain or host.endswith("." + domain), "Ranking URL outside competitor domain")
        klass = text(ranking, "evidence_class")
        require(klass in {"provider_export", "user_provided", "historical_sample", "simulation"}
                and klass == sources[sid].get("kind"), "Ranking evidence class differs from source")
        if domain.endswith(".example") or domain == "example":
            require(klass == "simulation", "Example-domain ranking must be simulated")
        position = text(ranking, "position", empty=True)
        require(not position or (position.isdigit() and int(position) > 0), "Ranking position must be positive or unknown")
        require(text(ranking, "market") == keywords[kid]["market"], "Ranking context differs from keyword")
        text(ranking, "observed_at")
        traffic_estimate(ranking)
        ranking_counts[klass] += 1

    for review in optional_records(plan, "competitor_page_reviews"):
        cid = text(review, "competitor_id")
        require(cid in competitors, "Unknown competitor page review")
        host = http_url(text(review, "url"))
        domain = text(competitors[cid], "domain").lower()
        require(host == domain or host.endswith("." + domain), "Reviewed URL outside competitor domain")
        for key in ("page_type", "menu_path", "summary"):
            text(review, key)
        status = text(review, "status")
        require(status in {"observed", "partial", "simulated", "blocked", "not_started"}, "Invalid competitor page review status")
        strings(review, "modules", required=status in {"observed", "partial", "simulated"})
        eids = evidence(review)
        if (domain.endswith(".example") or domain == "example") and status in {"observed", "partial", "simulated"}:
            require(status == "simulated", "Example-domain page reviews must be simulated")
        if status in {"observed", "partial"}:
            require(any(sources[s].get("kind") == "browser_observation" for s in eids),
                    "Page observation requires browser evidence")
        elif status == "simulated":
            require(any(sources[s].get("kind") == "simulation" for s in eids), "Simulated page review needs evidence")

    analysis_summary: Row = {"status": "not_provided"}
    if plan.get("keyword_analysis") is not None:
        analysis = obj(plan["keyword_analysis"])
        text(analysis, "method")
        tokenized = records(analysis, "tokenized_keywords")
        token_ids = [text(row, "keyword_id") for row in tokenized]
        require(len(token_ids) == len(set(token_ids)) and set(token_ids) == keywords.keys(),
                "Tokenized keyword coverage differs from input")
        for row in tokenized:
            strings(row, "tokens", required=True, unique=False)
            require(text(row, "keyword") == keywords[text(row, "keyword_id")]["keyword_original"],
                    "Tokenized original keyword differs")
        terms = records(analysis, "terms")
        for row in terms:
            text(row, "term")
            market, language = text(row, "market"), text(row, "language", empty=True)
            for key in ("tf", "df", "denominator_keywords", "ngram"):
                require(isinstance(row.get(key), int) and not isinstance(row.get(key), bool)
                        and int(str(row[key])) > 0, f"Invalid word-frequency {key}")
            ids = strings(row, "keyword_ids", required=True)
            require(set(ids) <= keywords.keys(), "Unknown word-frequency keyword reference")
            require(int(str(row["tf"])) >= int(str(row["df"])) == len(ids)
                    and int(str(row["df"])) <= int(str(row["denominator_keywords"])),
                    "Word-frequency TF/DF coverage mismatch")
            require(all((keywords[k]["market"], keywords[k]["language"]) == (market, language) for k in ids),
                    "Word-frequency mixes source contexts")
        candidates = index(records(analysis, "category_candidates"))
        for candidate in candidates.values():
            for key in ("dimension", "decision", "reason"):
                text(candidate, key)
            strings(candidate, "signal_terms", required=True)
            strings(candidate, "candidate_labels", required=True)
            require(set(strings(candidate, "keyword_ids", required=True)) <= keywords.keys(),
                    "Unknown category-candidate keyword")
            require(set(strings(candidate, "page_ids")) <= pages.keys(), "Unknown category-candidate page")
            evidence(candidate)
        analysis_summary = {"status": "provided", "tokenized_keywords": len(tokenized),
                            "single_word_entries": sum(row["ngram"] == 1 for row in terms),
                            "phrase_entries": sum(row["ngram"] != 1 for row in terms),
                            "candidate_dimensions": len(candidates)}

    design_validation = validate_design_details(plan, pages, templates, sources, competitors)
    bilingual_validation = validate_bilingual_structure(plan, pages, templates, navigation, keywords, sources)
    return {
        "structural_status": "pass",
        "pages": len(pages), "core_pages": sum(p.get("page_type") != "article" for p in pages.values()),
        "articles": sum(p.get("page_type") == "article" for p in pages.values()),
        "templates": len(templates), "keywords": len(keywords), "groups": len(groups),
        "page_states": dict(Counter(text(p, "state") for p in pages.values())),
        "template_instances": dict(usage),
        "keyword_dispositions": dict(Counter(text(d, "disposition") for d in decisions)),
        "navigation_items": len(navigation),
        "menu_groups_are_not_pages": sum(n.get("kind") == "group" for n in navigation.values()),
        "competitor_statuses": dict(Counter(text(c, "visit_status") for c in competitors.values())),
        "menus_observed": sum(c.get("menu_observed") is True for c in competitors.values()),
        "ranking_evidence": dict(ranking_counts),
        "keyword_analysis": analysis_summary,
        "design_specification": design_validation,
        "bilingual_structure": bilingual_validation,
        "research_complete": all(c.get("visit_status") == "observed" and c.get("menu_observed") is True
                                 for c in competitors.values()) and bool(competitors)
                                 and (plan.get("schema_version") not in DESIGN_VERSIONS or design_validation.get("complete") is True),
        "limitations": [
            "Structural validation does not verify evidence authenticity, semantic correctness or SEO outcomes.",
            "Template families are design briefs; no website templates have been implemented.",
            "Menu entries and page hierarchy are proposals; no website has been published.",
        ],
    }


def has_han(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0003134f]", value))


def bilingual(row: Row, key: str = "name") -> str:
    primary = text(row, key)
    translation = row.get(key + "_zh")
    return f"{primary}（{translation}）" if isinstance(translation, str) and translation != primary else primary


def validate_bilingual_structure(plan: Row, pages: dict[str, Row], templates: dict[str, Row],
                                 navigation: dict[str, Row], keywords: dict[str, dict[str, str]],
                                 sources: dict[str, Row]) -> Row:
    if plan.get("schema_version") != VERSION:
        return {"status": "legacy_not_required"}
    english = text(obj(plan["project"]), "language").lower().replace("_", "-").split("-")[0] == "en"

    def translated(row: Row, key: str) -> None:
        primary, chinese = text(row, key), text(row, key + "_zh")
        if english:
            require(not has_han(primary) and bool(re.search(r"[A-Za-z]", primary)),
                    f"English website text required: {key}")
        require(has_han(chinese), f"Chinese explanation required: {key}_zh")

    for page in pages.values():
        translated(page, "name")
        if english:
            require(page["name"] == page["name_en"], "Primary page name must equal name_en for English sites")
        translated(page, "cta")
        sections, translations = strings(page, "sections"), strings(page, "sections_zh")
        require(len(sections) == len(translations), "Section translation count differs")
        for primary, chinese in zip(sections, translations, strict=True):
            translated({"section": primary, "section_zh": chinese}, "section")
        if "design_brief" in page:
            translated(obj(page["design_brief"]), "primary_message")
    for nav in navigation.values():
        translated(nav, "label")
    for template in templates.values():
        translated(template, "name")
        spec = obj(template["design_spec"])
        for module in records(spec, "modules"):
            translated(module, "name")
        for field in records(spec, "fields"):
            translated(field, "label")
    for field in records(obj(plan["site_design"]), "fields"):
        translated(field, "label")
    analysis = obj(plan["keyword_analysis"]) if "keyword_analysis" in plan else {}
    for candidate in optional_records(analysis, "category_candidates"):
        labels, candidate_translations = strings(candidate, "candidate_labels"), strings(candidate, "candidate_labels_zh")
        require(len(labels) == len(candidate_translations), "Candidate translation count differs")
        for primary, translation in zip(labels, candidate_translations, strict=True):
            translated({"label": primary, "label_zh": translation}, "label")

    applications = [p for p in pages.values() if p.get("page_type") == "application"]
    for page in applications:
        parent = pages[text(page, "parent_id")]
        standalone = len(applications) == 1 and parent.get("page_type") == "home" and bool(page.get("standalone_reason"))
        require(parent.get("page_type") == "application_hub" or standalone,
                "Application detail requires an application_hub parent; a menu group is not a page")
        if standalone:
            text(page, "standalone_reason")
    for hub in (p for p in pages.values() if p.get("page_type") == "application_hub"):
        require(any(p.get("parent_id") == hub["id"] and p.get("page_type") in {"application", "application_hub"}
                    for p in pages.values()), "Application hub must lead to application pages")
    for nav in navigation.values():
        if nav.get("location") != "header" or nav.get("kind") != "page":
            continue
        page = pages[text(nav, "page_id")]
        if page.get("page_type") != "application" or page.get("standalone_reason"):
            continue
        ancestor = text(nav, "parent_id", empty=True)
        found = False
        while ancestor:
            item = navigation[ancestor]
            found = found or (item.get("kind") == "page" and item.get("page_id") == page["parent_id"])
            ancestor = text(item, "parent_id", empty=True)
        require(found, "Application navigation needs a clickable application_hub ancestor")

    attributes = index(optional_records(plan, "product_attributes"))
    studies = index(records(plan, "competitor_page_reviews"))
    for attribute in attributes.values():
        translated(attribute, "label")
        for key in ("definition", "measurement_note", "filter_reason", "filter_missing_policy"):
            text(attribute, key)
        text(attribute, "unit", empty=True)
        require(attribute.get("value_type") in {"number", "choice", "boolean", "text"}, "Unknown product attribute type")
        require(attribute.get("source_status") in {"available", "partial", "missing", "simulation"}, "Unknown attribute evidence status")
        require(attribute.get("filter_status") in {"proposed", "deferred", "not_applicable"}, "Unknown filter status")
        require(attribute.get("filter_control") in {"range", "select", "multi_select", "toggle", "none"}, "Unknown filter control")
        require(set(strings(attribute, "keyword_ids")) <= keywords.keys(), "Unknown product-attribute keyword")
        study_ids = strings(attribute, "study_ids")
        require(set(study_ids) <= studies.keys() and all(studies[s].get("status") in {"observed", "partial", "simulated"}
                                                       for s in study_ids), "Invalid product-attribute study")
        eids = strings(attribute, "evidence_ids", required=True)
        require(set(eids) <= sources.keys(), "Unknown product-attribute evidence")
        if attribute.get("source_status") == "available":
            require(any(sources[s].get("kind") == "user_provided" for s in eids)
                    and all(sources[s].get("kind") != "simulation" for s in eids),
                    "Available attribute values need own-company data")
        if attribute.get("source_status") == "simulation":
            require(obj(plan["project"]).get("mode") == "simulation", "Mock attributes cannot support a real project")
        targets = strings(attribute, "filter_page_ids")
        require(set(targets) <= pages.keys() and all(pages[p].get("page_type") in {"category", "category_overview"}
                                                   for p in targets), "Filter target must be a product category")
        options = records(attribute, "filter_options")
        for option in options:
            translated(option, "label")
            text(option, "value")
        require(len({text(o, "value") for o in options}) == len(options), "Duplicate filter option")
        if attribute.get("filter_status") == "proposed":
            require(bool(targets) and attribute.get("filter_control") != "none", "Proposed filter needs category and control")
            require(attribute.get("source_status") in {"available", "simulation"}, "Missing values cannot enable a filter")
            if attribute.get("filter_control") in {"select", "multi_select"}:
                require(bool(options), "Choice filter needs verified or explicitly simulated options")
        elif attribute.get("source_status") == "missing":
            require(not options, "Do not invent missing attribute options")
        for binding in records(attribute, "detail_bindings"):
            tid, fid = text(binding, "template_id"), text(binding, "field_id")
            require(tid in templates, "Unknown attribute detail template")
            template = templates[tid]
            require(bool(set(strings(template, "allowed_page_types")) & {"product", "attachment"}),
                    "Attribute detail binding must target a product template")
            fields = index(records(obj(template["design_spec"]), "fields"))
            require(fid in fields, "Unknown attribute detail field")
            require(fields[fid].get("kind") == attribute["value_type"], "Attribute type differs from detail field")
    return {"status": "specified", "primary_language": obj(plan["project"])["language"],
            "explanation_language": "zh-CN", "application_hubs": sum(p.get("page_type") == "application_hub" for p in pages.values()),
            "product_attributes": len(attributes)}


def summarize_ranked_urls(plan: Row, keywords: dict[str, dict[str, str]]) -> list[Row]:
    """Group URL evidence within the same source, market and dated snapshot, never across them."""
    competitors = index(records(plan, "competitors"))
    decisions = {text(d, "keyword_id"): d for d in records(plan, "decisions")}
    groups = index(records(plan, "groups"))
    buckets: dict[tuple[str, str, str, str, str], list[Row]] = {}
    for row in records(plan, "ranking_observations"):
        key = tuple(text(row, k) for k in ("competitor_id", "url", "market", "observed_at", "source_id"))
        # Fixed-length key keeps snapshots and providers separate.
        bucket_key = (key[0], key[1], key[2], key[3], key[4])
        buckets.setdefault(bucket_key, []).append(row)
    reviews = optional_records(plan, "competitor_page_reviews")
    result: list[Row] = []
    for (cid, url, market, date, source_id), rows in buckets.items():
        positions: dict[str, int] = {}
        estimates: dict[str, set[float]] = {}
        for row in rows:
            raw_position = text(row, "position", empty=True)
            if not raw_position or not 1 <= int(raw_position) <= 30:
                continue
            kid = text(row, "keyword_id")
            positions[kid] = min(positions.get(kid, 30), int(raw_position))
            values = estimates.setdefault(kid, set())
            estimate = traffic_estimate(row)
            if estimate is not None:
                values.add(estimate)
        matched = [r for r in reviews if r["competitor_id"] == cid and r["url"] == url]
        known = sum(bool(values) for values in estimates.values())
        conflicting = any(len(values) > 1 for values in estimates.values())
        complete = bool(positions) and known == len(positions) and not conflicting
        status = "conflicting" if conflicting else "complete" if complete else "partial" if known else "missing"
        group_ids = sorted({text(decisions[kid], "group_id") for kid in positions})
        result.append({
            "competitor_id": cid, "competitor": text(competitors[cid], "name"),
            "sample_group": competitors[cid].get("sample_group", "other"), "url": url,
            "market": market, "observed_at": date, "source_id": source_id,
            "evidence_class": rows[0]["evidence_class"], "ranking_rows": len(rows),
            "top30_keyword_count": len(positions), "top10_keyword_count": sum(p <= 10 for p in positions.values()),
            "best_position": min(positions.values()) if positions else "",
            "top30_keyword_ids": list(positions),
            "top30_keywords": [keywords[kid]["keyword_original"] for kid in positions],
            "buyer_groups": [text(groups[gid], "label") for gid in group_ids],
            "top30_estimated_traffic": sum(next(iter(v)) for v in estimates.values()) if complete else "",
            "traffic_coverage": f"{known}/{len(positions)}", "traffic_status": status,
            "review_status": list(dict.fromkeys(text(r, "status") for r in matched)) or ["not_visited"],
            "observed_page_types": list(dict.fromkeys(text(r, "page_type") for r in matched)),
            "menu_paths": list(dict.fromkeys(text(r, "menu_path") for r in matched)),
            "modules": list(dict.fromkeys(m for r in matched for m in strings(r, "modules"))),
            "review_notes": [text(r, "summary") for r in matched],
            "review_evidence_ids": list(dict.fromkeys(s for r in matched for s in strings(r, "evidence_ids"))),
        })
    return result


def value(cell: object) -> str:
    if isinstance(cell, dict) or isinstance(cell, list) and any(isinstance(v, dict) for v in cell):
        return json.dumps(cell, ensure_ascii=False)
    if isinstance(cell, list):
        return "\n".join(str(v) for v in cell)
    return str(cell) if cell is not None else ""


def write_csv(path: Path, rows: list[Row]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: value(v) for k, v in row.items()} for row in rows)


def dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def active_design_modules(template: Row, page: Row) -> list[Row]:
    if "design_spec" not in template:
        return []
    return [m for m in records(obj(template["design_spec"]), "modules")
            if not strings(m, "variants") or page["variant"] in strings(m, "variants")]


def factory_page_notes(plan: Row, pid: str) -> list[str]:
    if "factory_profile" not in plan:
        return []
    profile = obj(plan["factory_profile"])
    proofs = index(records(profile, "proof_items"))
    statuses = {"verified": "企业资料已核", "pending": "待补证据，不作已具备声明",
                "simulation": "MOCK假设，非真实资质", "not_applicable": "不适用"}
    result: list[str] = []
    for mapping in records(profile, "page_proof_map"):
        if mapping["page_id"] != pid:
            continue
        result += [text(mapping, "presentation"), "安排理由：" + text(mapping, "reason")]
        for ident in strings(mapping, "proof_ids"):
            item = proofs[ident]
            result.append(f"{item['claim']}；{statuses[text(item, 'status')]}；范围：{item['scope']}；"
                          f"呈现：{item['presentation']}；资料要求：{item['missing_action']}")
    return result


def design_prompt(plan: Row, page: Row, template: Row) -> str:
    if "design_brief" not in page or "design_spec" not in template:
        return ""
    brief, spec = obj(page["design_brief"]), obj(template["design_spec"])
    site, project = obj(plan["site_design"]), obj(plan["project"])
    factory = obj(plan["factory_profile"])
    fields = index(records(site, "fields") + records(spec, "fields"))
    studies = index(records(plan, "competitor_page_reviews"))
    notes = {text(n, "module_id"): text(n, "content_note") for n in records(brief, "module_notes")}
    lines = [f"# {bilingual(page)} · 完整页面设计简报", "",
             "请依据以下已规划内容生成完整页面视觉设计图。覆盖页头、所有适用模块与页脚；"
             "长页必要时拆成连续画面，不为画幅省略中下段。字段说明用于理解内容，不能把字段定义表直接画成网站模块。",
             "缺失事实使用明确占位；不补造工厂身份、产能、认证、客户、性能或交期。", "",
             f"页面：{page['path']}；模板：{bilingual(template)} / {page['variant']}；"
             f"目标市场：{project['market']}；网站语言：{project['language']}",
             "语言规则：按网站目标语言生成访客可见文案。英文站的菜单、页面标题、区块标题、"
             "产品参数标签、表单、按钮与占位文案均使用英文；中文翻译仅供审阅，不画进英文网站界面。",
             "资料性质：" + ("MOCK教学模拟，不是真实企业资料。" if project["mode"] == "simulation" else "以已登记资料为准，待补项仍是未知。"),
             f"企业角色：{factory['business_role']}；身份说明：{factory['identity_statement']}",
             f"目标：{page['objective']}", f"读者：{brief['audience']}",
             f"核心信息：{bilingual(brief, 'primary_message')}", f"文案要求：{brief['copy_direction']}",
             f"视觉重点：{brief['emphasis']}", f"图片方向：{brief['image_direction']}",
             f"主要行动：{bilingual(page, 'cta')}", "", "## 整站视觉与共用区域", "",
             f"方向：{site['visual_direction']}", f"字体层级：{site['typography']}",
             f"留白与密度：{site['spacing']}", f"图片风格：{site['image_style']}",
             f"页头：{site['header']}", f"页脚：{site['footer']}", f"手机导航：{site['mobile_navigation']}", "",
             "导航标签：" + " / ".join(bilingual(n, "label") for n in records(plan, "navigation")
                                    if n.get("location") == "header" and not n.get("parent_id")), ""]
    proof_notes = factory_page_notes(plan, text(page, "id"))
    if proof_notes:
        lines += ["## 本页的工厂身份与采购信任信息", "", *["- " + v for v in proof_notes], ""]
    lines += ["## 页面模块顺序与设计要求", ""]
    used: list[str] = [text(f, "id") for f in records(site, "fields")]
    for number, module in enumerate(active_design_modules(template, page), 1):
        mid = text(module, "id")
        used += strings(module, "field_ids")
        lines += [f"### {number}. {bilingual(module)}", "", f"解决的问题：{module['purpose']}",
                  f"条件：{'必需' if module['required'] else '按条件展示'}；{module['condition']}",
                  *["- 内容：" + v for v in strings(module, "content")],
                  f"桌面：{module['desktop_layout']}", f"手机：{module['mobile_layout']}",
                  f"交互与状态：{module['interaction']}", f"采用理由：{module['decision']}"]
        if mid in notes:
            lines.append("本页专属内容：" + notes[mid])
        for sid in strings(module, "study_ids"):
            study = studies[sid]
            lines.append(f"参考：{study['url']}（{study['status']}，第{study['review_round']}轮；只借鉴信息组织）")
        lines += ["字段：" + "、".join(bilingual(fields[f], "label") for f in strings(module, "field_ids")), ""]
    lines += ["## 字段和素材要求", ""]
    for fid in dict.fromkeys(used):
        field = fields[fid]
        lines += [f"- {bilingual(field, 'label')}：{field['kind']}；{'必填' if field['required'] else '可选'}；"
                  f"范围={field['scope']}；状态={field['value_status']}；格式={field['format']}；"
                  f"来源={field['source']}；缺失时={field['missing_policy']}"]
    relevant_attributes = [a for a in optional_records(plan, "product_attributes")
                           if page["id"] in strings(a, "filter_page_ids") or
                           any(b.get("template_id") == template["id"] for b in records(a, "detail_bindings"))]
    if relevant_attributes:
        lines += ["", "## 产品属性与筛选设计", ""]
        for attribute in relevant_attributes:
            use = "类目筛选" if page["id"] in strings(attribute, "filter_page_ids") else "产品详情字段"
            lines += [f"- {bilingual(attribute, 'label')}：{use}；类型={attribute['value_type']}；"
                      f"单位={attribute['unit'] or '不适用'}；资料={attribute['source_status']}；"
                      f"筛选安排={attribute['filter_status']}；{attribute['definition']}；"
                      f"口径={attribute['measurement_note']}；缺失处理={attribute['filter_missing_policy']}"]
            if use == "类目筛选":
                lines += ["  选项：" + (" / ".join(bilingual(o, "label") for o in records(attribute, "filter_options"))
                                      or "待真实机型数据确认；不编造范围或选项")]
    lines += ["", "## 本页独有内容", "", *["- " + v for v in strings(page, "unique_content")],
              "", "## 尚需补充", "", *["- " + v for v in strings(page, "fact_gaps")],
              *["- 研究：" + v for v in strings(spec, "research_gaps")], "",
              "验收画稿：模块和顺序完整；本页主题明确；桌面与手机有阅读优先级；"
              "采购CTA清楚；事实与占位可区分；没有照抄竞争对手品牌、图片、文案或未经证实的资质。"]
    return "\n".join(lines) + "\n"


def research_markdown(plan: Row) -> str:
    project = obj(plan["project"])
    coverage = design_research_coverage(plan)
    lines = ["# 竞争对手核心页面调研与设计取舍", "", text(project, "scope_note"), "",
             "研究逐轮保留；本报告按已有记录汇总。未访问、受阻、部分访问与模拟均不能计为真实完整拆解。", "",
             "## 核心URL覆盖", "", f"已登记{coverage['target_count']}个目标；最新状态：{coverage['latest_status_counts']}。", ""]
    for target in records(coverage, "targets"):
        lines += [f"- {target['competitor_id']} · {target['page_type']} · {target['url']}",
                  f"  - 用途：{target['reason']}；最新：{target['latest_status']}；记录：{target['latest_review_id']}"]
    lines += ["", "## 逐页拆解与回访", ""]
    for review in optional_records(plan, "competitor_page_reviews"):
        lines += [f"### {review.get('id', '')} · {review['page_type']} · 第{review.get('review_round', '?')}轮", "",
                  f"URL：{review['url']}", f"状态：{review['status']}；时间：{review.get('observed_at', '未记录')}",
                  f"菜单位置：{review['menu_path']}", f"结论：{review['summary']}",
                  f"内容任务：{review.get('content_summary', '旧记录未细分')}",
                  f"桌面观察：{review.get('desktop_notes', '未记录')}", f"手机观察：{review.get('mobile_notes', '未记录')}", ""]
        for i, module in enumerate(optional_records(review, "module_details"), 1):
            lines += [f"{i}. **{module['name']}**", f"   - 内容：{module['content']}",
                      f"   - 布局：{module['layout']}", "   - 可见字段：" + "、".join(strings(module, "fields")),
                      f"   - 交互：{module['interaction']}", f"   - 评价/取舍：{module['assessment']}"]
        lines += ["", "来源ID：" + "、".join(strings(review, "evidence_ids")),
                  "下一轮：" + str(review.get("follow_up") or "本条暂无新增问题；仍以覆盖清单为准"), ""]
    lines += ["## 从研究到本站模板", ""]
    for template in records(plan, "templates"):
        lines += [f"### {template['id']} {bilingual(template)}", "", text(template, "rationale"), ""]
        if "design_spec" not in template:
            continue
        spec = obj(template["design_spec"])
        for module in records(spec, "modules"):
            lines += [f"- {bilingual(module)}：{module['decision']}；参考研究："
                      + ("、".join(strings(module, "study_ids")) or "尚无直接实访依据，见来源/缺口")]
        lines += ["", "不采用的做法：", *["- " + v for v in strings(spec, "rejected_patterns")],
                  "待回访/待核：", *["- " + v for v in strings(spec, "research_gaps")], ""]
    lines += ["## 资料索引", ""]
    for source in records(plan, "sources"):
        lines += [f"- {source['id']} · {source['kind']} · {source['label']}：{source['reference']}；范围：{source['scope']}"]
    return "\n".join(lines) + "\n"


def viewer_payload(payload: Row, keywords: dict[str, dict[str, str]]) -> Row:
    """Keep the HTML review concise; complete data stays in the JSON/CSV outputs."""
    result = dict(payload)
    scope: list[Row] = []
    needed_ids: set[str] = set()
    for key, filename in (("decisions", "keyword-decisions.csv"), ("ranking_observations", "ranking-observations.csv"),
                          ("ranked_urls", "ranked-url-summary.csv"), ("url_evidence", "competitor-url-summary.csv")):
        rows = records(payload, key)
        shown = rows[:500]
        result[key] = shown
        scope.append({"dataset": key, "shown": len(shown), "total": len(rows), "file": filename})
        for row in shown:
            if isinstance(row.get("keyword_id"), str):
                needed_ids.add(text(row, "keyword_id"))
            for list_key in ("top30_keywords", "buyer_groups", "modules", "review_notes"):
                if list_key in row:
                    row[list_key] = strings(row, list_key, unique=False)[:20]
    for key in ("pages", "articles"):
        rows = records(payload, key)
        for row in rows:
            covered = strings(row, "covered_keywords", unique=False)
            row["covered_keyword_count"] = len(covered)
            row["covered_keywords"] = covered[:30]
            refs = strings(row, "reference_urls")
            row["reference_url_count"] = len(refs)
            row["reference_urls"] = refs[:20]
        result[key] = rows
    if payload.get("keyword_analysis") is not None:
        analysis = obj(payload["keyword_analysis"])
        terms = records(analysis, "terms")
        selected = sorted(terms, key=lambda r: (-int(str(r["df"])), -int(str(r["tf"])), text(r, "term")))[:500]
        for term in selected:
            ids = strings(term, "keyword_ids")
            term["keyword_count_total"] = len(ids)
            term["keyword_ids"] = ids[:20]
            needed_ids.update(ids[:20])
        tokenized = records(analysis, "tokenized_keywords")
        candidates = records(analysis, "category_candidates")
        for candidate in candidates:
            ids = strings(candidate, "keyword_ids")
            candidate["keyword_count_total"] = len(ids)
            candidate["keyword_ids"] = ids[:20]
        result["keyword_analysis"] = {**analysis, "terms": selected, "tokenized_keywords": tokenized[:20],
                                      "category_candidates": candidates}
        scope += [{"dataset": "terms", "shown": len(selected), "total": len(terms), "file": "term-frequency.csv"},
                  {"dataset": "tokenized_keywords", "shown": min(20, len(tokenized)), "total": len(tokenized),
                   "file": "tokenized-keywords.csv"}]
    result["keyword_lookup"] = {kid: keywords[kid]["keyword_original"] for kid in needed_ids if kid in keywords}
    result["viewer_scope"] = scope
    return result


def compile_architecture(plan_path: Path, keywords_path: Path, out: Path, analysis_dir: Path | None = None) -> Row:
    require(not out.exists(), "Choose a new output directory; existing outputs are preserved")
    raw: object = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    plan = obj(raw)
    keywords = read_keywords(keywords_path)
    validation = validate(plan, keywords)
    delivery = collect_analysis(analysis_dir, keywords_path, set(index(records(plan, "competitors"))))
    validation["tool_version"] = TOOL_VERSION
    validation["analysis_delivery"] = delivery.summary()
    pages = index(records(plan, "pages"))
    templates = index(records(plan, "templates"))
    decisions = records(plan, "decisions")
    ranking_rows = records(plan, "ranking_observations")
    project = obj(plan["project"])

    def breadcrumbs(page: Row, key: str = "name") -> list[str]:
        chain = [text(page, key)]
        parent = text(page, "parent_id", empty=True)
        while parent:
            chain.insert(0, text(pages[parent], key))
            parent = text(pages[parent], "parent_id", empty=True)
        return chain

    page_rows: list[Row] = []
    for page in pages.values():
        primary = text(page, "primary_keyword_id", empty=True)
        words = [keywords[text(d, "keyword_id")]["keyword_original"] for d in decisions if d.get("page_id") == page["id"]]
        assigned_ids = {text(d, "keyword_id") for d in decisions if d.get("page_id") == page["id"]}
        reference_urls = list(dict.fromkeys(text(r, "url") for r in ranking_rows
                                           if r["keyword_id"] in assigned_ids and text(r, "position", empty=True)
                                           and 1 <= int(text(r, "position")) <= 30))
        page_rows.append({**page, "breadcrumbs": breadcrumbs(page), "breadcrumbs_en": breadcrumbs(page, "name_en"),
                          **({"breadcrumbs_zh": breadcrumbs(page, "name_zh")} if plan.get("schema_version") == VERSION else {}),
                          "url": text(project, "site_url").rstrip("/") + text(page, "path"),
                          "primary_keyword": keywords[primary]["keyword_original"] if primary else "",
                          "covered_keywords": words, "reference_urls": reference_urls,
                          "factory_notes": factory_page_notes(plan, text(page, "id")),
                          "design_prompt": design_prompt(plan, page, templates[text(page, "template_id")])})
    template_rows: list[Row] = []
    for template in records(plan, "templates"):
        instance_ids = [text(p, "id") for p in pages.values() if p.get("template_id") == template["id"]]
        proposed_count = sum(pages[pid].get("state") == "proposed" for pid in instance_ids)
        article_count = sum(pages[pid].get("page_type") == "article" for pid in instance_ids)
        template_rows.append({**template, "instance_count": len(instance_ids), "page_ids": instance_ids,
                              "core_instance_count": len(instance_ids) - article_count, "article_count": article_count,
                              "proposed_instance_count": proposed_count,
                              "conditional_instance_count": len(instance_ids) - proposed_count})
    decision_rows = [{**d, "keyword": keywords[text(d, "keyword_id")]["keyword_original"],
                      "market": keywords[text(d, "keyword_id")]["market"],
                      "language": keywords[text(d, "keyword_id")]["language"]} for d in decisions]
    validation["inputs"] = {
        str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in (plan_path, keywords_path)
    }
    asset = Path(__file__).resolve().parents[1] / "assets/architecture-view.html"
    template_html = asset.read_text(encoding="utf-8")
    require(template_html.count("__ARCHITECTURE_DATA__") == 1, "Viewer data placeholder missing or repeated")
    core_rows = [p for p in page_rows if p.get("page_type") != "article"]
    article_rows = [p for p in page_rows if p.get("page_type") == "article"]
    ranked_urls = summarize_ranked_urls(plan, keywords)
    payload = {**plan, "pages": core_rows, "articles": article_rows, "ranked_urls": ranked_urls,
               "url_evidence": [dict(r) for r in delivery.urls],
               "templates": template_rows, "decisions": decision_rows, "validation": validation}
    encoded = json.dumps(viewer_payload(payload, keywords), ensure_ascii=False).replace("<", "\\u003c").replace(
        "\u2028", "\\u2028").replace("\u2029", "\\u2029")
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".architecture-", dir=out.parent) as temporary:
        stage = Path(temporary) / "result"
        stage.mkdir()
        delivery.write(stage)
        dump(stage / "architecture-plan.json", plan)
        dump(stage / "validation.json", validation)
        for filename, rows in (("pages", core_rows), ("content-plan", article_rows), ("templates", template_rows),
                               ("navigation", records(plan, "navigation")), ("keyword-decisions", decision_rows),
                               ("ranking-observations", records(plan, "ranking_observations")),
                               ("ranked-url-summary", ranked_urls)):
            write_csv(stage / f"{filename}.csv", rows)
        if plan.get("keyword_analysis") is not None:
            analysis = obj(plan["keyword_analysis"])
            for filename, key in (("term-frequency", "terms"), ("tokenized-keywords", "tokenized_keywords"),
                                  ("category-candidates", "category_candidates")):
                write_csv(stage / f"{filename}.csv", records(analysis, key))
        (stage / "index.html").write_text(template_html.replace("__ARCHITECTURE_DATA__", encoded), encoding="utf-8")
        if plan.get("schema_version") in DESIGN_VERSIONS:
            module_rows: list[Row] = []
            field_rows = [{"template_id": "SITE", **row} for row in records(obj(plan["site_design"]), "fields")]
            for template in template_rows:
                spec = obj(template["design_spec"])
                module_rows.extend({"template_id": template["id"], "order": n, **m}
                                   for n, m in enumerate(records(spec, "modules"), 1))
                field_rows.extend({"template_id": template["id"], **f} for f in records(spec, "fields"))
            write_csv(stage / "design-modules.csv", module_rows)
            write_csv(stage / "design-fields.csv", field_rows)
            (stage / "research-report.md").write_text(research_markdown(plan), encoding="utf-8")
            (stage / "design-briefs.md").write_text("\n\n---\n\n".join(
                text(p, "design_prompt") for p in page_rows if p.get("design_prompt")), encoding="utf-8")
        if plan.get("schema_version") == VERSION:
            write_csv(stage / "product-attributes.csv", optional_records(plan, "product_attributes"))
        tree_lines = ["# " + text(project, "name"), "", text(project, "scope_note"), ""]

        def walk(parent: str, depth: int) -> None:
            for page in core_rows:
                if page.get("parent_id") == parent:
                    tree_lines.append("  " * depth + f"- {bilingual(page)} [{page['id']}] · {page['template_id']} · {page['path']}")
                    walk(text(page, "id"), depth + 1)

        walk("", 0)
        (stage / "mindmap.md").write_text("\n".join(tree_lines) + "\n", encoding="utf-8")
        handoff = ["# 页面与模板设计交接", "", text(project, "scope_note"), "",
                   f"本次规划{len(core_rows)}个核心页面、{len(article_rows)}个独立文章选题、"
                   f"{len(template_rows)}类页面设计。文章详单见content-plan.csv，不进入架构图或菜单。", "",
                   f"其中{sum(p.get('state') == 'conditional' for p in pages.values())}个节点待业务确认；"
                   "待核页面先保留资料要求，不进入正式导航。所有规划页仍需真实内容后才能发布。", "",
                   "## 交给 WordPress Builder Harness", "",
                   "当前阶段：架构与模板设计简报。下一步由wordpress-builder接续到核心页/模板代表页设计；"
                   "本资料未生成视觉稿、客户WordPress模板或已发布文章。", "",
                   "入口：index.html、mindmap.md、pages.csv、navigation.csv、templates.csv；"
                   "文章选题单独见content-plan.csv，竞品页面依据见ranked-url-summary.csv。", "",
                   "完整分析的附件状态见validation.json中的analysis_delivery；文件索引见analysis-index.md。"
                   "附带排名分析时，competitor-url-summary.csv含独立整页流量及无词明细的URL，"
                   "page-traffic-observations.csv保留整页原记录；两类流量不能相加。", "",
                   "按实际模板及变体选代表页设计，产品或文章增加时复用合适模板。"
                   "结合客户现有项目确认内容模型、原生字段与ACF字段的职责；博客正文保留正常文章编辑入口。"
                   "没有项目事实时不预填工程路径、模板文件名、CPT或字段key。", "",
                   "复用已确认的客户资料、已有设计与授权；新站准备和视觉选样按Builder当前流程接续。", "",
                   "## 建议设计顺序", ""]
        for template in sorted(template_rows, key=lambda row: int(str(row["design_order"]))):
            handoff += [f"### {template['id']} {bilingual(template)}", "",
                        f"实例：{template['instance_count']}（初步规划{template['proposed_instance_count']}，"
                        f"待核{template['conditional_instance_count']}）；样板："
                        f"{value(template['representative_page_ids'])}", "",
                        "模块：" + " → ".join(strings(template, "modules")), "",
                        "字段：" + "、".join(strings(template, "fields")), "", text(template, "rationale"), ""]
        handoff += ["## 逐页内容与资料", ""]
        if plan.get("schema_version") in DESIGN_VERSIONS:
            handoff += ["完整模块、字段、桌面/手机布局及可复制的设计任务见index.html的设计简报，"
                        "以及design-briefs.md；原始拆解与逐轮取舍见research-report.md。", "",
                        "工厂身份按factory_profile的真实业务角色表达；各项能力和证书只使用已核企业证据，"
                        "模拟和待补项不得升级为真实承诺。", ""]
        representative_articles = {pid for t in template_rows for pid in strings(t, "representative_page_ids")
                                   if pages[pid].get("page_type") == "article"}
        for page in core_rows + [p for p in article_rows if p["id"] in representative_articles]:
            handoff += [f"### {page['id']} {bilingual(page)}", "", text(page, "objective"), "",
                        "独有内容：" + "；".join(strings(page, "unique_content")), "",
                        "待补：" + ("；".join(strings(page, "fact_gaps")) or "按发布前事实复核"), "",
                        "输入范围的排名URL（来源性质见证据）：" +
                        ("；".join(strings(page, "reference_urls")) or "本输入范围暂无关联"), ""]
        (stage / "design-handoff.md").write_text("\n".join(handoff), encoding="utf-8")
        require(not out.exists(), "Output appeared during execution")
        stage.rename(out)
    return validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--keywords", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path,
                        help="Selected current analysis runs to copy with the final HTML, including page traffic")
    args = parser.parse_args()
    try:
        result = compile_architecture(args.plan, args.keywords, args.out, args.analysis_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
