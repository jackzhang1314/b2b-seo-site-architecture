#!/usr/bin/env python3
"""Frequency computation, semantic handoff validation, and source-faithful topics."""
from __future__ import annotations

import argparse
import csv
import hashlib
from itertools import chain
import json
import re
import sys
import unicodedata
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

VERSION = "1.1.1"
# Keep decimal numbers, units, hyphenated model codes and diameter/size separators.
TOKEN = re.compile(r"[^\W_]+(?:[.\-/'×][^\W_]+)*", re.UNICODE)


def text_workbook(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    """Spreadsheet reading copy: explicit text cells, no formulas or external links.

    The sibling CSV remains the exact machine input. XML-incompatible control
    characters and Excel-oversized values are replaced/truncated in this view only.
    """
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with ZipFile(path.with_suffix(".xlsx"), "w", ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                      '<Default Extension="xml" ContentType="application/xml"/>'
                      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                      '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        book.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr("xl/workbook.xml", f'<workbook xmlns="{main}" xmlns:r="{rel}">'
                      '<sheets><sheet name="阅读表" sheetId="1" r:id="rId1"/></sheets></workbook>')
        book.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        with book.open("xl/worksheets/sheet1.xml", "w") as sheet:
            sheet.write(f'<worksheet xmlns="{main}"><sheetViews><sheetView workbookViewId="0">'
                        '<pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><sheetData>'.encode())
            values = chain([fields], ([row.get(key, "") for key in fields] for row in rows))
            for index, row_values in enumerate(values, 1):
                sheet.write(f'<row r="{index}">'.encode())
                for value in row_values:
                    clean = "".join(c if c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF or
                                    0xE000 <= ord(c) <= 0xFFFD or ord(c) >= 0x10000 else "\uFFFD" for c in value)
                    if len(clean) > 32767:
                        clean = clean[:32700] + "…[阅读截断；完整值见CSV]"
                    sheet.write(('<c t="inlineStr"><is><t xml:space="preserve">' +
                                 escape(clean).replace("\r", "&#13;") + '</t></is></c>').encode("utf-8"))
                sheet.write(b'</row>')
            sheet.write(b'</sheetData></worksheet>')


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("Expected object with string keys")
    return {str(k): v for k, v in value.items()}


def items(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Expected list")
    return list(value)


def strings(value: object) -> dict[str, str]:
    data = obj(value)
    if not all(isinstance(v, str) for v in data.values()):
        raise ValueError("Expected string values")
    return {k: str(v) for k, v in data.items()}


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def stable_id(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def read_table(path: Path, encoding: str = "auto", delimiter: str = "auto") -> Iterator[tuple[int, dict[str, str]]]:
    if encoding == "auto":
        with path.open("rb") as stream:
            bom = stream.read(3)
        encoding = "utf-16" if bom[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    with path.open(encoding=encoding, newline="") as stream:
        if delimiter == "auto":
            sample = stream.read(16384)
            stream.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
            except csv.Error:
                delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        reader = csv.DictReader(stream, delimiter=delimiter)
        headers = reader.fieldnames or []
        if not headers or len(headers) != len(set(headers)):
            raise ValueError(f"Missing/duplicate headers: {path}")
        footer_started = False
        for record, row in enumerate(reader, 1):
            present = [v for v in row.values() if isinstance(v, str)]
            if len(present) == 1 and any(v is None for v in row.values()):
                notice = present[0]
                known_footer = bool(re.fullmatch(r"You can export only first \d+ rows available for your subscription\.", notice)) or notice in {
                    "To export more data, request your custom plan.",
                    "See plans and prices https://www.semrush.com/prices/",
                }
                if known_footer:
                    footer_started = True
                    yield record, {"__export_footer__": notice}
                    continue
            if footer_started:
                raise ValueError("Unexpected data after recognized export footer")
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"Unequal field count: {path}, record {record}")
            yield record, {str(k): str(v) for k, v in row.items()}


def keywords(path: Path) -> list[dict[str, str]]:
    rows = [row for _, row in read_table(path)]
    required = {"keyword_id", "keyword_original", "keyword_normalized", "market", "language"}
    if not rows or not all(required <= set(row) and row["keyword_id"] and row["keyword_normalized"] for row in rows):
        raise ValueError("Expected nonempty canonical keywords.csv from library Skill")
    if len({r["keyword_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate keyword_id")
    keys = {(r["keyword_normalized"], r["market"], r["language"]) for r in rows}
    if len(keys) != len(rows):
        raise ValueError("Duplicate normalized keyword/market/language")
    return rows


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_table(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)
    text_workbook(path, rows, fields)


@contextmanager
def output_dir(destination: Path) -> Iterator[Path]:
    if destination.exists():
        raise ValueError(f"Output already exists; choose a new run directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".cluster-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "result"
        staging.mkdir()
        yield staging
        if destination.exists():
            raise ValueError("Output appeared during run")
        staging.rename(destination)


def snapshot(path: Path, command: str) -> dict[str, object]:
    return {"version": VERSION, "command": command, "input_path": str(path.resolve()),
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def frequency(path: Path, out: Path, ngram: int, min_df: int, stop_path: Path | None, facet_path: Path | None) -> None:
    rows = keywords(path)
    if not 1 <= ngram <= 5 or min_df < 1:
        raise ValueError("ngram must be 1..5; min-df must be positive")
    stops = {normalize(s) for s in stop_path.read_text().splitlines() if s.strip()} if stop_path else set()
    facet_def = strings(json.loads(facet_path.read_text())) if facet_path else {}
    facets = {normalize(k): v for k, v in facet_def.items()}
    tf: Counter[tuple[str, str, str]] = Counter()
    df: Counter[tuple[str, str, str]] = Counter()
    denominators: Counter[tuple[str, str]] = Counter()
    members: list[dict[str, str]] = []
    tokenless: list[str] = []
    for row in rows:
        market, language = row["market"], row["language"]
        denominators[(market, language)] += 1
        tokens = TOKEN.findall(row["keyword_normalized"])
        if not tokens:
            tokenless.append(row["keyword_id"])
        phrases: Counter[str] = Counter()
        for length in range(1, ngram + 1):
            for i in range(len(tokens) - length + 1):
                phrase_tokens = tokens[i:i + length]
                # Do not delete stopwords first: that would invent adjacency.
                if any(t in stops for t in phrase_tokens):
                    continue
                phrases[" ".join(phrase_tokens)] += 1
        for phrase, count in phrases.items():
            tf[(market, language, phrase)] += count
            df[(market, language, phrase)] += 1
            if phrase in facets:
                members.append({"keyword_id": row["keyword_id"], "term_or_phrase": phrase,
                    "facet_type": facets[phrase], "assignment_reason": "exact token/contiguous phrase match; analyst-supplied facet dictionary"})
    terms = [{"market": m, "language": lang, "term_or_phrase": term, "tf": str(tf[(m, lang, term)]),
              "df": str(count), "denominator_keywords": str(denominators[(m, lang)]), "facet_type": facets.get(term, "")}
             for (m, lang, term), count in sorted(df.items(), key=lambda x: (-x[1], x[0])) if count >= min_df]
    with output_dir(out) as stage:
        write_table(stage / "term-frequency.csv", terms, ["market", "language", "term_or_phrase", "tf", "df", "denominator_keywords", "facet_type"])
        write_table(stage / "keyword-facets.csv", members, ["keyword_id", "term_or_phrase", "facet_type", "assignment_reason"])
        write_json(stage / "run.json", {**snapshot(path, "frequency"), "keywords": len(rows), "terms_emitted": len(terms),
            "terms_before_min_df": len(df), "ngram": ngram, "min_df": min_df, "stopwords": sorted(stops),
            "facet_dictionary": facets, "tokenless_ids": tokenless,
            "tokenizer": TOKEN.pattern, "normalization": "no stemming/synonym replacement; contiguous token ngrams",
            "limits": ["Word-frequency view, not search volume or same-page clustering", "Chinese/Japanese require task-specific segmentation; whitespace-less runs remain whole tokens"]})


def prepare(path: Path, out: Path, batch_size: int) -> None:
    rows = keywords(path)
    if not 1 <= batch_size <= 500:
        raise ValueError("batch-size must be 1..500")
    with output_dir(out) as stage:
        for i in range(0, len(rows), batch_size):
            write_json(stage / f"batch-{i // batch_size + 1:03}.json", [
                {k: row[k] for k in ("keyword_id", "keyword_original", "market", "language")}
                for row in rows[i:i + batch_size]])
        write_json(stage / "run.json", {**snapshot(path, "semantic-prepare"), "keywords": len(rows),
            "batch_size": batch_size, "batches": (len(rows) + batch_size - 1) // batch_size,
            "status": "awaiting Codex semantic judgments; no clustering performed"})


def semantic(path: Path, assignment: Path, out: Path) -> None:
    rows = keywords(path)
    known = {r["keyword_id"]: r for r in rows}
    proposal = obj(json.loads(assignment.read_text(encoding="utf-8-sig")))
    method = str(proposal.get("method", "")).strip()
    model = str(proposal.get("model", "")).strip()
    if not method or not model:
        raise ValueError("Record actual method and model; use 'not exposed' if exact version unavailable")
    clusters: dict[str, dict[str, str]] = {}
    for item in items(proposal.get("clusters")):
        cluster = strings(item)
        required = ("cluster_id", "label", "buyer_task", "intent", "entity_scope", "constraints", "rationale")
        if not all(cluster.get(k, "").strip() for k in required):
            raise ValueError("Incomplete semantic cluster definition")
        cid = cluster["cluster_id"]
        if cid in clusters:
            raise ValueError("Duplicate cluster_id")
        clusters[cid] = {k: cluster[k] for k in required}
    members: list[dict[str, str]] = []
    seen: set[str] = set()
    cluster_context: dict[str, set[tuple[str, str]]] = {}
    used: set[str] = set()
    for item in items(proposal.get("memberships")):
        m = strings(item)
        kid, cid = m.get("keyword_id", ""), m.get("cluster_id", "")
        if kid not in known or kid in seen:
            raise ValueError(f"Unknown or repeated primary keyword ID: {kid}")
        if cid and cid not in clusters:
            raise ValueError(f"Unknown cluster ID: {cid}")
        if m.get("review_state") not in {"assigned", "review", "outlier"} or not m.get("assignment_reason", "").strip():
            raise ValueError("Every member needs an explicit state and reason")
        if m["review_state"] == "assigned" and not cid:
            raise ValueError("Assigned member must name a cluster")
        if cid:
            used.add(cid)
            cluster_context.setdefault(cid, set()).add((known[kid]["market"], known[kid]["language"]))
        seen.add(kid)
        members.append({k: m[k] for k in ("keyword_id", "cluster_id", "assignment_reason", "review_state")})
    if seen != set(known):
        raise ValueError(f"Unaccounted keywords: {len(set(known) - seen)}")
    if any(len(c) != 1 for c in cluster_context.values()):
        raise ValueError("A primary cluster cannot silently mix market/language contexts")
    if used != set(clusters):
        raise ValueError("Unused cluster definitions")
    with output_dir(out) as stage:
        fields = ["cluster_id", "label", "buyer_task", "intent", "entity_scope", "constraints", "rationale"]
        write_table(stage / "semantic-clusters.csv", list(clusters.values()), fields)
        write_table(stage / "semantic-memberships.csv", members, ["keyword_id", "cluster_id", "assignment_reason", "review_state"])
        write_json(stage / "proposal.json", proposal)
        write_json(stage / "run.json", {**snapshot(path, "semantic-check"), "assignment_sha256": hashlib.sha256(assignment.read_bytes()).hexdigest(),
            "method": method, "model": model, "evidence_class": "model_inference", "keywords": len(rows), "clusters": len(clusters),
            "states": dict(Counter(m["review_state"] for m in members)), "validation": "membership integrity only; semantic correctness requires task review",
            "same_page_decisions": "not performed"})


def provider(path: Path, manifest_path: Path, out: Path) -> None:
    rows = keywords(path)
    lookup = {(r["keyword_normalized"], r["market"], r["language"]): r["keyword_id"] for r in rows}
    config = obj(json.loads(manifest_path.read_text(encoding="utf-8-sig")))
    sources = items(config.get("sources"))
    if not sources:
        raise ValueError("At least one topic source required")
    topics: dict[str, dict[str, str]] = {}
    members: list[dict[str, str]] = []
    registers: list[dict[str, object]] = []
    source_ids: set[str] = set()
    with output_dir(out) as stage:
        with (stage / "raw-topics.jsonl").open("w", encoding="utf-8") as raw_stream:
            for entry in sources:
                source = obj(entry)
                sid = str(source.get("source_id", ""))
                if not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or sid in source_ids:
                    raise ValueError("Unique source_id required")
                source_ids.add(sid)
                vendor = str(source.get("provider", "unverified"))
                verified = source.get("source_verified", False)
                hierarchy = source.get("hierarchy_explicit", False)
                if type(verified) is not bool or type(hierarchy) is not bool:
                    raise ValueError("source_verified/hierarchy_explicit must be JSON booleans")
                if vendor not in {"ahrefs", "semrush", "unverified"}:
                    raise ValueError("provider must be ahrefs, semrush or unverified")
                if verified and (vendor == "unverified" or not str(source.get("provenance_ref", "")).strip()):
                    raise ValueError("Verified topic source requires vendor and provenance_ref")
                missing_values = source.get("missing_topic_values", ["N/A"])
                if not isinstance(missing_values, list) or not all(isinstance(v, str) for v in missing_values):
                    raise ValueError("missing_topic_values must be a list of strings")
                missing_labels = {"", *(normalize(v) for v in missing_values)}
                levels = [strings(v) for v in items(source.get("levels"))]
                if not levels or any(not x.get("field") or not x.get("level") for x in levels):
                    raise ValueError("Explicit level/field mappings required")
                if len({x['level'] for x in levels}) != len(levels):
                    raise ValueError("Duplicate topic level")
                columns = strings(source.get("columns"))
                context = strings(source.get("context", {}))
                if "keyword" not in columns or not set(columns) <= {"keyword", "market", "language"}:
                    raise ValueError("Invalid keyword/context column map")
                src = manifest_path.parent / str(source["path"])
                file_hash = hashlib.sha256(src.read_bytes()).hexdigest()
                seen: set[str] = set()
                record_count = 0
                footer_count = 0
                for record, raw in read_table(src, str(source.get("encoding", "auto")), str(source.get("delimiter", "auto"))):
                    record_count += 1
                    ref = f"{sid}:record:{record}"
                    raw_stream.write(json.dumps({"source_ref": ref, "fields": raw}, ensure_ascii=False) + "\n")
                    if "__export_footer__" in raw:
                        footer_count += 1
                        continue
                    required = set(columns.values()) | {x["field"] for x in levels} | {x["id_field"] for x in levels if x.get("id_field")} | {x["type_field"] for x in levels if x.get("type_field")}
                    if not required <= set(raw):
                        raise ValueError(f"Missing topic columns: {required - set(raw)}")
                    market = (raw[columns["market"]] if "market" in columns else context.get("market", "")).strip().upper()
                    language = (raw[columns["language"]] if "language" in columns else context.get("language", "")).strip().lower()
                    word = raw[columns["keyword"]]
                    kid = lookup.get((normalize(word), market, language), "")
                    if kid:
                        seen.add(kid)
                    parent = ""
                    for level in levels:
                        label = raw[level["field"]]
                        key = ""
                        missing = normalize(label) in missing_labels
                        state = "missing" if missing else ("observed" if verified else "imported_unverified")
                        if not kid:
                            state = "unmatched_keyword"
                        if not missing:
                            official_id = raw[level["id_field"]] if level.get("id_field") else ""
                            raw_type = raw[level["type_field"]] if level.get("type_field") else ""
                            key = "local_" + stable_id(vendor, sid, file_hash, market, language, level["level"], parent, label, official_id, raw_type)
                            topics.setdefault(key, {"local_topic_key": key, "provider": vendor, "provider_topic_id": official_id,
                                "raw_label": label, "raw_field": level["field"], "raw_type": raw_type, "topic_level": level["level"],
                                "parent_local_key": parent, "market": market, "language": language,
                                "snapshot": str(source.get("observed_at", "")), "source_ref": ref,
                                "evidence_state": "observed" if verified else "imported_unverified"})
                        members.append({"keyword_id": kid, "keyword_original": word, "provider": vendor, "local_topic_key": key,
                            "topic_level": level["level"], "value_state": state, "source_ref": ref})
                        parent = key if hierarchy else ""
                for row in rows:
                    if row["keyword_id"] not in seen:
                        members.append({"keyword_id": row["keyword_id"], "keyword_original": row["keyword_original"],
                            "provider": vendor, "local_topic_key": "", "topic_level": "", "value_state": "not_returned", "source_ref": sid})
                registers.append({**source, "missing_topic_values": missing_values,
                                  "resolved_path": str(src.resolve()), "sha256": file_hash,
                                  "records": record_count, "excluded_export_footers": footer_count, "matched_keyword_ids": len(seen)})
        write_table(stage / "provider-topics.csv", list(topics.values()), ["local_topic_key", "provider", "provider_topic_id", "raw_label", "raw_field", "raw_type",
            "topic_level", "parent_local_key", "market", "language", "snapshot", "source_ref", "evidence_state"])
        write_table(stage / "provider-topic-memberships.csv", members, ["keyword_id", "keyword_original", "provider", "local_topic_key", "topic_level", "value_state", "source_ref"])
        write_json(stage / "source-register.json", registers)
        write_json(stage / "run.json", {**snapshot(path, "provider"), "keywords": len(rows), "topics": len(topics),
            "membership_states": dict(Counter(m["value_state"] for m in members)),
            "limits": ["source_verified is a supplied provenance attestation, not automatic vendor authentication", "No topic labels generated", "No API called", "Not a same-page decision"]})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("frequency", "semantic-prepare", "semantic-check", "provider"):
        p = sub.add_parser(name)
        p.add_argument("--keywords", required=True, type=Path)
        p.add_argument("--out", required=True, type=Path)
        if name == "frequency":
            p.add_argument("--ngram", type=int, default=3)
            p.add_argument("--min-df", type=int, default=1)
            p.add_argument("--stopwords", type=Path)
            p.add_argument("--facets", type=Path)
        elif name == "semantic-prepare":
            p.add_argument("--batch-size", type=int, default=80)
        elif name == "semantic-check":
            p.add_argument("--assignments", required=True, type=Path)
        else:
            p.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "frequency":
            frequency(args.keywords, args.out, args.ngram, args.min_df, args.stopwords, args.facets)
        elif args.command == "semantic-prepare":
            prepare(args.keywords, args.out, args.batch_size)
        elif args.command == "semantic-check":
            semantic(args.keywords, args.assignments, args.out)
        else:
            provider(args.keywords, args.manifest, args.out)
    except (ValueError, KeyError, OSError, UnicodeError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "completed", "output": str(args.out.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
