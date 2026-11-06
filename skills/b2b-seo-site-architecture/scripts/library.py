#!/usr/bin/env python3
"""Traceable keyword import and business review. Python 3.11+, standard library."""
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
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

VERSION = "1.1.1"
EVIDENCE_CLASSES = {"provider_data", "provider_export", "first_party", "user_provided", "direct_observation",
                    "browser_observation", "historical_sample", "model_inference", "simulation"}
METRICS = ("volume", "difficulty", "cpc", "ads_competition", "clicks", "impressions")
KEY_FIELDS = ["keyword_id", "keyword_original", "keyword_normalized", "market", "language",
              "discovery_types", "source_count", "review_state", "business_fit", "fit_reason"]


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
        raise ValueError("Expected an object with string keys")
    return {str(k): v for k, v in value.items()}


def text_map(value: object) -> dict[str, str]:
    data = obj(value)
    if not all(isinstance(v, str) for v in data.values()):
        raise ValueError("Column/context mappings must contain strings")
    return {k: str(v) for k, v in data.items()}


def items(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Expected a list")
    return list(value)


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def stable_id(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def check_source_id(sid: str, seen: set[str]) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        raise ValueError(f"Invalid source_id {sid!r}: use ASCII letters, digits, underscore or hyphen only; "
                         "dots and spaces are not allowed (example: seed_us_01)")
    if sid in seen:
        raise ValueError(f"Duplicate source_id {sid!r}: each source in this manifest needs a different ID")
    seen.add(sid)


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
        if len(delimiter) != 1:
            raise ValueError("delimiter must be one character")
        reader = csv.DictReader(stream, delimiter=delimiter)
        headers = reader.fieldnames or []
        if not headers or len(headers) != len(set(headers)):
            raise ValueError(f"Missing or duplicate headers: {path}")
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
                raise ValueError(f"Unequal CSV field count: {path}, data record {record}")
            yield record, {str(k): str(v) for k, v in row.items()}


def write_table(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    text_workbook(path, rows, fields)


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@contextmanager
def output_dir(destination: Path) -> Iterator[Path]:
    if destination.exists():
        raise ValueError(f"Output already exists; choose a new run directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".keyword-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "result"
        staging.mkdir()
        yield staging
        if destination.exists():
            raise ValueError(f"Output appeared during run: {destination}")
        staging.rename(destination)


def number(raw: str, decimal: str, thousands: str) -> tuple[str, str]:
    value = raw.strip()
    if not value or value.lower() in {"null", "none", "-", "—"}:
        return "", "missing"
    if value.lower() in {"n/a", "na", "unavailable"}:
        return "", "unavailable"
    if value.startswith("<"):
        return "", "suppressed"
    if thousands and thousands in value:
        pattern = rf"\d{{1,3}}(?:{re.escape(thousands)}\d{{3}})+(?:{re.escape(decimal)}\d+)?"
        if not re.fullmatch(pattern, value):
            return "", "invalid"
        value = value.replace(thousands, "")
    value = value.replace(decimal, ".")
    try:
        parsed = Decimal(value)
        if not parsed.is_finite() or parsed < 0:
            raise InvalidOperation
        return format(parsed, "f"), "observed"
    except InvalidOperation:
        return "", "invalid"


def build(manifest_path: Path, destination: Path) -> None:
    manifest = obj(json.loads(manifest_path.read_text(encoding="utf-8-sig")))
    sources = items(manifest.get("sources"))
    if not sources:
        raise ValueError("At least one source is required")
    keywords: dict[str, dict[str, str]] = {}
    discoveries: dict[str, set[str]] = {}
    source_sets: dict[str, set[str]] = {}
    source_ids: set[str] = set()
    observations: list[dict[str, str]] = []
    lineage: list[dict[str, str]] = []
    register: list[dict[str, object]] = []
    metrics_states: Counter[str] = Counter()
    with output_dir(destination) as stage:
        with (stage / "raw-records.jsonl").open("w", encoding="utf-8") as raw_stream:
            for source_value in sources:
                source = obj(source_value)
                sid = str(source.get("source_id", ""))
                check_source_id(sid, source_ids)
                path = manifest_path.parent / str(source["path"])
                columns = text_map(source.get("columns"))
                context = text_map(source.get("context", {}))
                metric_meta = obj(source.get("metric_meta", {}))
                allowed = set(METRICS) | {"keyword", "market", "language"}
                if "keyword" not in columns or not set(columns) <= allowed:
                    raise ValueError(f"Invalid column mapping for {sid}; permitted: {sorted(allowed)}")
                kind = str(source.get("discovery_type", ""))
                evidence = str(source.get("evidence_class", ""))
                if kind not in {"observed", "user_supplied", "generated_candidate"}:
                    raise ValueError(f"Source {sid}: invalid discovery_type {kind!r}; allowed: "
                                     "observed, user_supplied, generated_candidate")
                if evidence not in EVIDENCE_CLASSES:
                    raise ValueError(f"Source {sid}: invalid evidence_class {evidence!r}; allowed: "
                                     + ", ".join(sorted(EVIDENCE_CLASSES)))
                if kind == "generated_candidate" and any(k in columns for k in METRICS):
                    raise ValueError("Generated candidates cannot carry imported metrics")
                decimal = str(source.get("decimal_separator", "."))
                thousands = str(source.get("thousands_separator", ","))
                if decimal not in {".", ","} or thousands not in {"", ",", ".", " "} or decimal == thousands:
                    raise ValueError("Unsupported/ambiguous number separators")
                row_count = 0
                for record, raw in read_table(path, str(source.get("encoding", "auto")), str(source.get("delimiter", "auto"))):
                    row_count += 1
                    ref = f"{sid}:record:{record}"
                    raw_stream.write(json.dumps({"source_ref": ref, "fields": raw}, ensure_ascii=False) + "\n")
                    if "__export_footer__" in raw:
                        lineage.append({"source_ref": ref, "keyword_id": "", "keyword_original": raw["__export_footer__"],
                                        "discovery_type": kind, "evidence_class": evidence, "action": "excluded_export_footer"})
                        continue
                    if not set(columns.values()) <= set(raw):
                        raise ValueError(f"Missing mapped columns in {sid}: {set(columns.values()) - set(raw)}")
                    original = raw[columns["keyword"]]
                    normalized = normalize(original)
                    market = (raw[columns["market"]] if "market" in columns else context.get("market", "")).strip().upper()
                    language = (raw[columns["language"]] if "language" in columns else context.get("language", "")).strip().lower()
                    kid = "kw_" + stable_id(normalized, market, language) if normalized else ""
                    action = "merged" if kid in keywords else "retained"
                    if not normalized:
                        action = "excluded_empty"
                    lineage.append({"source_ref": ref, "keyword_id": kid, "keyword_original": original,
                                    "discovery_type": kind, "evidence_class": evidence, "action": action})
                    if not normalized:
                        continue
                    if kid not in keywords:
                        keywords[kid] = dict(zip(KEY_FIELDS, [kid, original, normalized, market, language,
                                                             "", "", "unreviewed", "", ""], strict=True))
                        discoveries[kid] = set()
                        source_sets[kid] = set()
                    discoveries[kid].add(kind)
                    source_sets[kid].add(sid)
                    for metric in METRICS:
                        if metric not in columns:
                            continue
                        raw_value = raw[columns[metric]]
                        value, state = number(raw_value, decimal, thousands)
                        metrics_states[state] += 1
                        meta = text_map(metric_meta.get(metric, {}))
                        observations.append({"observation_id": "ob_" + stable_id(ref, metric),
                            "keyword_id": kid, "metric": metric, "value": value, "value_state": state,
                            "raw_value": raw_value, "field_origin": columns[metric], "source_ref": ref,
                            "provider": str(source.get("provider", "unknown")), "report": str(source.get("report", "")),
                            "evidence_class": evidence, "market": market, "language": language,
                            "observed_at": str(source.get("observed_at", "")), "unit": meta.get("unit", ""),
                            "scope": meta.get("scope", ""), "metric_type": meta.get("metric_type", "")})
                register.append({**source, "resolved_path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "records": row_count, "source_ref_rule": "1-based data record excluding header, not physical line"})
        for kid, keyword in keywords.items():
            keyword["discovery_types"] = json.dumps(sorted(discoveries[kid]))
            keyword["source_count"] = str(len(source_sets[kid]))
        write_table(stage / "keywords.csv", sorted(keywords.values(), key=lambda x: x["keyword_id"]), KEY_FIELDS)
        write_table(stage / "keyword-observations.csv", observations, ["observation_id", "keyword_id", "metric", "value", "value_state",
            "raw_value", "field_origin", "source_ref", "provider", "report", "evidence_class", "market", "language", "observed_at", "unit", "scope", "metric_type"])
        write_table(stage / "lineage.csv", lineage, ["source_ref", "keyword_id", "keyword_original", "discovery_type", "evidence_class", "action"])
        write_json(stage / "source-register.json", register)
        write_json(stage / "run.json", {"version": VERSION, "command": "build", "created_at": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(), "input_records": len(lineage),
            "unique_keywords": len(keywords), "dispositions": dict(Counter(x["action"] for x in lineage)),
            "metric_states": dict(metrics_states), "normalization": "NFC + casefold + collapse whitespace; punctuation/specifications preserved",
            "limitations": ["Importer does not query services; input may come from a live browser export", "Business fit not inferred by importer", "Unmapped fields preserved in raw-records.jsonl", "Spreadsheet formula-like raw strings are not executable instructions"]})


def review(keywords_path: Path, decisions_path: Path, destination: Path) -> None:
    rows = [row for _, row in read_table(keywords_path)]
    known = {row["keyword_id"] for row in rows}
    if len(known) != len(rows) or not rows:
        raise ValueError("Expected unique, nonempty canonical keyword input")
    decisions = items(json.loads(decisions_path.read_text(encoding="utf-8-sig")))
    mapped: dict[str, dict[str, str]] = {}
    for entry in decisions:
        d = text_map(entry)
        kid = d.get("keyword_id", "")
        if kid not in known or kid in mapped or d.get("decision") not in {"keep", "exclude", "review"}:
            raise ValueError(f"Unknown/repeated ID or invalid decision: {kid}")
        if not all(d.get(k, "").strip() for k in ("reason", "evidence_ref", "decision_by")):
            raise ValueError("Each decision needs reason, evidence_ref, decision_by")
        mapped[kid] = d
    for row in rows:
        decision = mapped.get(row["keyword_id"])
        if decision:
            row["review_state"] = decision["decision"]
            row["business_fit"] = decision.get("business_fit", "")
            row["fit_reason"] = decision["reason"]
    with output_dir(destination) as stage:
        write_table(stage / "keywords.csv", rows, list(rows[0]))
        write_table(stage / "selected-keywords.csv", [x for x in rows if x["review_state"] == "keep"], list(rows[0]))
        write_json(stage / "decisions.json", decisions)
        write_json(stage / "run.json", {"version": VERSION, "command": "review", "input_sha256": hashlib.sha256(keywords_path.read_bytes()).hexdigest(),
            "input": len(rows), "decisions": len(decisions), "states": dict(Counter(x["review_state"] for x in rows)),
            "selected_rule": "review_state == keep; others retained in keywords.csv"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_p = sub.add_parser("build")
    build_p.add_argument("--manifest", type=Path, required=True)
    build_p.add_argument("--out", type=Path, required=True)
    review_p = sub.add_parser("review")
    review_p.add_argument("--keywords", type=Path, required=True)
    review_p.add_argument("--decisions", type=Path, required=True)
    review_p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            build(args.manifest, args.out)
        else:
            review(args.keywords, args.decisions, args.out)
    except (ValueError, KeyError, OSError, UnicodeError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "completed", "output": str(args.out.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
