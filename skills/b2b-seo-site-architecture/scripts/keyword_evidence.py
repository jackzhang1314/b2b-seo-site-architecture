#!/usr/bin/env python3
"""Deterministic term drill-down and competitor URL evidence, without model calls."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from cluster import TOKEN, keywords
from library import normalize, number, obj, output_dir, read_table, stable_id, text_map, write_json, write_table

VERSION = "0.4.0"
CONTEXT = ("provider", "competitor_id", "market", "language", "observed_at", "url")
RANK_FIELDS = ["source_ref", "source_id", *CONTEXT, "evidence_class", "keyword_id", "keyword_original",
               "position", "position_raw", "estimated_traffic", "traffic_state", "traffic_raw", "issues"]
PAGE_FIELDS = ["source_ref", "source_id", *CONTEXT, "evidence_class", "page_estimated_traffic", "traffic_state", "traffic_raw"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def valid_url(url: str, domain: str, evidence: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        _ = parsed.port
        return (parsed.scheme in {"https", "http"} and bool(host) and not parsed.username and not parsed.password
                and (host == domain or host.endswith("." + domain))
                and (not host.endswith(".example") or evidence == "simulation"))
    except ValueError:
        return False


def consistent_metric(values: list[str]) -> tuple[str, str]:
    observed = {Decimal(v) for v in values if v}
    if len(observed) > 1:
        return "", "conflicting"
    if not observed:
        return "", "missing"
    if any(not v for v in values):
        return "", "partial"
    return format(next(iter(observed)), "f"), "complete"


def url_summary(rankings: list[dict[str, str]], pages: list[dict[str, str]]) -> list[dict[str, str]]:
    ranked: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    page_metrics: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rankings:
        if "invalid_url" not in row["issues"]:
            ranked[tuple(row[k] for k in CONTEXT)].append(row)
    for row in pages:
        page_metrics[tuple(row[k] for k in CONTEXT)].append(row)
    summaries: list[dict[str, str]] = []
    for key in sorted(ranked.keys() | page_metrics.keys()):
        rows, metrics = ranked.get(key, []), page_metrics.get(key, [])
        known: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            if row["keyword_id"]:
                known[row["keyword_id"]].append(row)
        positions = {kid: min(int(r["position"]) for r in items if r["position"])
                     for kid, items in known.items() if any(r["position"] for r in items)}
        values = [consistent_metric([r["estimated_traffic"] for r in items]) for items in known.values()]
        traffic_state = ("conflicting" if any(s == "conflicting" for _, s in values) else
                         "complete" if values and all(s == "complete" for _, s in values) else
                         "partial" if any(v or s == "partial" for v, s in values) else "missing")
        # Unknown/empty keywords also make the full received-range traffic total incomplete.
        if any(not r["keyword_id"] for r in rows) and traffic_state == "complete":
            traffic_state = "partial"
        subtotal = sum((Decimal(v) for v, _ in values if v), Decimal(0))
        page_value, page_state = consistent_metric([r["page_estimated_traffic"] for r in metrics])
        summaries.append({**dict(zip(CONTEXT, key, strict=True)),
            "url_group_id": "url_" + stable_id(*key), "source_ids": json.dumps(sorted({r["source_id"] for r in rows + metrics})),
            "evidence_classes": json.dumps(sorted({r["evidence_class"] for r in rows + metrics})),
            "ranking_rows_received": str(len(rows)), "unknown_keyword_rows": str(sum(not r["keyword_id"] for r in rows)),
            "ranked_keyword_count": str(len(known)), "top30_keyword_count": str(sum(v <= 30 for v in positions.values())),
            "top10_keyword_count": str(sum(v <= 10 for v in positions.values())),
            "best_position": str(min(positions.values())) if positions else "",
            "received_keyword_traffic": format(subtotal, "f") if traffic_state == "complete" else "",
            "keyword_traffic_status": traffic_state,
            "page_estimated_traffic": page_value, "page_traffic_status": page_state,
            "sample_keywords": json.dumps([items[0]["keyword_original"] for items in list(known.values())[:20]], ensure_ascii=False),
            "page_type": "not_reviewed", "same_page_decision": "not_performed",
            "scope": "Received export scope only; keyword traffic and page traffic are separate, never added."})
    return summaries


def rankings(keywords_path: Path, manifest_path: Path, out: Path) -> None:
    words = keywords(keywords_path)
    known = {(r["keyword_normalized"], r["market"], r["language"]): r["keyword_id"] for r in words}
    manifest = obj(json.loads(manifest_path.read_text(encoding="utf-8-sig")))
    sources = manifest.get("sources")
    require(isinstance(sources, list) and bool(sources), "At least one evidence source required")
    if not isinstance(sources, list):
        raise ValueError("Expected source list")
    rank_rows: list[dict[str, str]] = []
    page_rows: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    register: list[dict[str, object]] = []
    seen_sources: set[str] = set()
    with output_dir(out) as stage:
        with (stage / "raw-records.jsonl").open("w", encoding="utf-8") as raw_stream:
            for item in sources:
                source = obj(item)
                sid = str(source.get("source_id", ""))
                require(bool(re.fullmatch(r"[A-Za-z0-9_-]+", sid)) and sid not in seen_sources, "Unique source_id required")
                seen_sources.add(sid)
                kind = str(source.get("kind", ""))
                require(kind in {"rankings", "pages"}, "Source kind must be rankings or pages")
                context = {k: str(source.get(k, "")).strip() for k in CONTEXT if k != "url"}
                context["market"], context["language"] = context["market"].upper(), context["language"].lower()
                require(all(context.values()), "Explicit provider, competitor, market, language and snapshot required")
                domain = str(source.get("domain", "")).strip().lower()
                require(bool(domain) and "/" not in domain, "Competitor domain required")
                evidence = str(source.get("evidence_class", ""))
                require(evidence in {"provider_export", "user_provided", "historical_sample", "simulation"}, "Explicit evidence class required")
                columns = text_map(source.get("columns"))
                allowed = {"keyword", "position", "url", "estimated_traffic"} if kind == "rankings" else {"url", "page_estimated_traffic"}
                required = {"keyword", "position", "url"} if kind == "rankings" else {"url", "page_estimated_traffic"}
                require(required <= columns.keys() and columns.keys() <= allowed, "Invalid evidence column mapping; separate page and keyword traffic")
                path = manifest_path.parent / str(source["path"])
                decimal, thousands = str(source.get("decimal_separator", ".")), str(source.get("thousands_separator", ","))
                require(decimal in {".", ","} and thousands in {"", ",", ".", " "} and decimal != thousands,
                        "Unsupported number separators")
                count = 0
                for record, raw in read_table(path, str(source.get("encoding", "auto")), str(source.get("delimiter", "auto"))):
                    count += 1
                    ref = f"{sid}:record:{record}"
                    raw_stream.write(json.dumps({"source_ref": ref, "fields": raw}, ensure_ascii=False) + "\n")
                    if "__export_footer__" in raw:
                        issues.append({"source_ref": ref, "issue": "export_footer", "detail": raw["__export_footer__"]})
                        continue
                    require(set(columns.values()) <= raw.keys(), f"Missing mapped columns: {sid}")
                    url = raw[columns["url"]].strip()
                    row_issues: list[str] = []
                    if not valid_url(url, domain, evidence):
                        row_issues.append("invalid_url")
                    metric = "estimated_traffic" if kind == "rankings" else "page_estimated_traffic"
                    raw_traffic = raw[columns[metric]] if metric in columns else ""
                    traffic, traffic_state = number(raw_traffic, decimal, thousands)
                    if traffic_state in {"invalid", "suppressed"}:
                        row_issues.append("traffic_" + traffic_state)
                    common = {"source_ref": ref, "source_id": sid, **context, "url": url, "evidence_class": evidence,
                              "traffic_state": traffic_state, "traffic_raw": raw_traffic}
                    if kind == "rankings":
                        word = raw[columns["keyword"]]
                        kid = known.get((normalize(word), context["market"], context["language"]), "")
                        if not kid:
                            row_issues.append("unknown_keyword" if normalize(word) else "empty_keyword")
                        position_raw = raw[columns["position"]]
                        position = position_raw.strip()
                        if not position.isdigit() or int(position) < 1:
                            row_issues.append("missing_position" if not position else "invalid_position")
                            position = ""
                        rank_rows.append({**common, "keyword_id": kid, "keyword_original": word, "position": position,
                                          "position_raw": position_raw, "estimated_traffic": traffic,
                                          "issues": ";".join(row_issues)})
                    elif "invalid_url" not in row_issues:
                        page_rows.append({**common, "page_estimated_traffic": traffic})
                    for issue in row_issues:
                        issues.append({"source_ref": ref, "issue": issue, "detail": "Original row preserved in raw-records.jsonl"})
                register.append({**source, "resolved_path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "records": count})
        summaries = url_summary(rank_rows, page_rows)
        write_table(stage / "ranking-observations.csv", rank_rows, RANK_FIELDS)
        write_table(stage / "page-traffic-observations.csv", page_rows, PAGE_FIELDS)
        write_table(stage / "competitor-url-summary.csv", summaries, list(summaries[0]) if summaries else [*CONTEXT])
        members = [{"url_group_id": "url_" + stable_id(*(r[k] for k in CONTEXT)), "keyword_id": r["keyword_id"],
                    "keyword_original": r["keyword_original"], "source_ref": r["source_ref"], "position": r["position"]}
                   for r in rank_rows if "invalid_url" not in r["issues"]]
        write_table(stage / "keyword-url-memberships.csv", members,
                    ["url_group_id", "keyword_id", "keyword_original", "source_ref", "position"])
        write_table(stage / "input-issues.csv", issues, ["source_ref", "issue", "detail"])
        write_json(stage / "source-register.json", register)
        write_json(stage / "run.json", {"version": VERSION, "command": "rankings", "keywords": len(words),
            "input_records": sum(int(str(r["records"])) for r in register), "ranking_rows": len(rank_rows),
            "page_metric_rows": len(page_rows), "url_snapshots": len(summaries), "issues": dict(Counter(r["issue"] for r in issues)),
            "keywords_sha256": hashlib.sha256(keywords_path.read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "limits": ["No external service queried; all received positions retained, with Top 30 as a separate count",
                       "URL grouping is evidence about existing pages, not a decision to merge our pages",
                       "Page types and business relevance require review; article and commercial pages both retained",
                       "Unknown keyword rows must be reconciled with the complete keyword library"]})


def drilldown(keywords_path: Path, term: str, out: Path) -> None:
    rows = keywords(keywords_path)
    term = normalize(term)
    require(bool(term) and TOKEN.findall(term) == [term], "Drill-down requires one literal token")
    numeric_variant = re.compile(r"\d+(?:\.\d+)?-?" + re.escape(term) + r"\Z")
    variants: Counter[tuple[str, str, str, str]] = Counter()
    co_terms: Counter[tuple[str, str, str]] = Counter()
    members: list[dict[str, str]] = []
    denominators: Counter[tuple[str, str]] = Counter()
    for row in rows:
        context = (row["market"], row["language"])
        denominators[context] += 1
        tokens = TOKEN.findall(row["keyword_normalized"])
        matches: set[tuple[str, str]] = set()
        for i, token in enumerate(tokens):
            if token == term:
                previous = tokens[i - 1] if i else ""
                label = previous + " " + term if re.fullmatch(r"\d+(?:\.\d+)?", previous) else term
                matches.add((label, "exact_token"))
            elif numeric_variant.fullmatch(token):
                matches.add((token, "numeric_spelling_variant_not_unit_equivalence"))
        if not matches:
            continue
        co_terms.update((context[0], context[1], token) for token in set(tokens) if token != term)
        for label, method in sorted(matches):
            variants[(*context, label, method)] += 1
            members.append({"keyword_id": row["keyword_id"], "keyword_original": row["keyword_original"],
                            "market": context[0], "language": context[1], "term": term, "variant": label,
                            "match_method": method, "tokens": json.dumps(tokens, ensure_ascii=False)})
    variant_rows = [{"market": m, "language": lang, "variant": variant, "match_method": method,
                     "df": str(count), "denominator_keywords": str(denominators[(m, lang)])}
                    for (m, lang, variant, method), count in sorted(variants.items(), key=lambda x: (-x[1], x[0]))]
    co_rows = [{"market": m, "language": lang, "term": token, "df_with_focus_term": str(count)}
               for (m, lang, token), count in sorted(co_terms.items(), key=lambda x: (-x[1], x[0]))]
    with output_dir(out) as stage:
        write_table(stage / "term-members.csv", members,
                    ["keyword_id", "keyword_original", "market", "language", "term", "variant", "match_method", "tokens"])
        write_table(stage / "term-variants.csv", variant_rows,
                    ["market", "language", "variant", "match_method", "df", "denominator_keywords"])
        write_table(stage / "cooccurring-terms.csv", co_rows, ["market", "language", "term", "df_with_focus_term"])
        write_json(stage / "run.json", {"version": VERSION, "command": "drilldown", "term": term, "keywords": len(rows),
            "matched_keywords": len({r["keyword_id"] for r in members}), "variant_memberships": len(members),
            "keywords_sha256": hashlib.sha256(keywords_path.read_bytes()).hexdigest(),
            "limits": ["Literal token and numeric spelling cues; not semantic groups or product facts",
                       "No conversion between ton, tonne, t, kg, short ton or long ton",
                       "Independent pages and filters require business, page and product-data review"]})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    rank = sub.add_parser("rankings")
    rank.add_argument("--keywords", type=Path, required=True)
    rank.add_argument("--manifest", type=Path, required=True)
    rank.add_argument("--out", type=Path, required=True)
    drill = sub.add_parser("drilldown")
    drill.add_argument("--keywords", type=Path, required=True)
    drill.add_argument("--term", required=True)
    drill.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "rankings":
            rankings(args.keywords, args.manifest, args.out)
        else:
            drilldown(args.keywords, args.term, args.out)
    except (ValueError, KeyError, OSError, UnicodeError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "completed", "output": str(args.out.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
