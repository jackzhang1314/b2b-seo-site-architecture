"""Build a portable catalogue of received files, original rows and complete query analysis."""
from __future__ import annotations

import csv
import json
import shutil
from collections import Counter, defaultdict
from itertools import groupby
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from cluster import TOKEN
from delivery import AnalysisDelivery, digest
from library import items, read_table, stable_id, text_map
from plan_records import Row, obj, optional_records, records, require, text

CHUNK_SIZE = 100


def script_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c").replace(
        "\u2028", "\\u2028").replace("\u2029", "\\u2029")


def export_rows(stage: Path, entry: Row, rows: Iterable[tuple[int, dict[str, str]]]) -> None:
    """Keep the first rows in the HTML; further pages work via local script files even under file://."""
    key = text(entry, "id")
    headers: list[str] = []
    chunk: list[list[object]] = []
    count, sequence = 0, 0

    def flush() -> None:
        nonlocal sequence
        if not sequence:
            entry["preview"] = list(chunk)
        else:
            dest = stage / "table-data" / key
            dest.mkdir(parents=True, exist_ok=True)
            call = f"window.workbenchRows({script_json(key)},{sequence},{script_json(chunk)});"
            (dest / f"{sequence}.js").write_text(call, encoding="utf-8")
        sequence += 1
        chunk.clear()

    for number, fields in rows:
        if not headers:
            headers = list(fields)
        if "__export_footer__" in fields:
            cells = [fields["__export_footer__"]]
        else:
            cells = [fields.get(h, "") for h in headers]
        chunk.append([number, *cells])
        count += 1
        if len(chunk) == CHUNK_SIZE:
            flush()
    if chunk:
        flush()
    entry.update(columns=headers, row_count=count, chunk_size=CHUNK_SIZE)


def source_catalog(stage: Path, delivery: AnalysisDelivery, keywords_path: Path,
                   materials: Path | None) -> list[Row]:
    catalogue: list[Row] = []
    shutil.copyfile(keywords_path, stage / "clean-keywords.csv")
    for run in delivery.runs:
        family = str(run["command"])
        if family not in {"build", "rankings"}:
            continue
        folder = stage / str(run["directory"])
        register = [obj(s) for s in items(json.loads((folder / "source-register.json").read_text(encoding="utf-8-sig")))]
        by_source: dict[str, Row] = {}
        for source in register:
            sid = text(source, "source_id")
            name = Path(text(source, "path")).name
            entry: Row = {"id": stable_id(family, sid), "name": name, "source_id": sid, "family": family,
                          "kind": source.get("kind", "keywords"), "provider": source.get("provider", ""),
                          "observed_at": source.get("observed_at", ""), "evidence_class": source.get("evidence_class", ""),
                          "sha256": source.get("sha256", ""), "row_count": source["records"], "download": "",
                          "columns": [], "preview": [], "chunk_size": CHUNK_SIZE}
            if "received_file" in source:
                blob = (folder / text(source, "received_file")).resolve()
                require(blob.is_relative_to((folder / "originals").resolve()) and blob.is_file()
                        and digest(blob) == source["sha256"], f"Original file differs from intake: {sid}")
                entry["download"] = quote(blob.relative_to(stage.resolve()).as_posix())
            by_source[sid] = entry
            catalogue.append(entry)
        raw_path = folder / "raw-records.jsonl"
        with raw_path.open(encoding="utf-8") as stream:
            raw = (obj(json.loads(line)) for line in stream)
            for sid, lines in groupby(raw, lambda x: text(x, "source_ref").rsplit(":record:", 1)[0]):
                entry = by_source[sid]
                expected = entry["row_count"]
                export_rows(stage, entry, ((int(text(x, "source_ref").rsplit(":", 1)[1]), text_map(x["fields"])) for x in lines))
                require(entry["row_count"] == expected, f"Source row count differs: {sid}")
    if materials is not None:
        manifest = obj(json.loads(materials.read_text(encoding="utf-8-sig")))
        for n, item in enumerate(records(manifest, "files"), 1):
            path = materials.parent / text(item, "path")
            require(path.is_file() and path.suffix.lower() in
                    {".csv", ".tsv", ".xlsx", ".xls", ".ods", ".txt", ".md", ".pdf", ".docx"},
                    f"Expected a supplied table or business document: {path.name}")
            key = stable_id("supplied", str(n), path.name)
            dest = stage / "supplied" / key / path.name
            dest.parent.mkdir(parents=True)
            shutil.copyfile(path, dest)
            entry = {"id": key, "name": path.name, "label": item.get("label", path.name), "family": "supplied",
                     "source_id": str(item.get("source_id", f"file_{n}")), "kind": item.get("kind", "material"),
                     "provider": item.get("provider", ""), "observed_at": item.get("observed_at", ""),
                     "evidence_class": item.get("evidence_class", "user_provided"),
                     "download": quote(dest.relative_to(stage).as_posix()), "sha256": digest(dest), "row_count": None,
                     "columns": [], "preview": [], "chunk_size": CHUNK_SIZE}
            if dest.suffix.lower() in {".csv", ".tsv"}:
                export_rows(stage, entry, read_table(dest, str(item.get("encoding", "auto")), str(item.get("delimiter", "auto"))))
            catalogue.append(entry)
    return catalogue


def keyword_analysis(plan: Row, keywords: dict[str, dict[str, str]], decisions: dict[str, Row]) -> Row:
    tf: Counter[tuple[str, str, tuple[str, ...]]] = Counter()
    df: Counter[tuple[str, str, tuple[str, ...]]] = Counter()
    locales: Counter[tuple[str, str]] = Counter()
    token_rows: list[list[str]] = []
    intent_members: dict[str, list[str]] = defaultdict(list)
    for kid, word in keywords.items():
        market, lang = word["market"], word["language"]
        locales[market, lang] += 1
        tokens = TOKEN.findall(word["keyword_normalized"])
        token_rows.append(tokens)
        seen = set()
        for n in (1, 2, 3):
            for i in range(len(tokens) - n + 1):
                key = (market, lang, tuple(tokens[i:i+n]))
                tf[key] += 1
                seen.add(key)
        df.update(seen)
        intent_members[text(decisions[kid], "group_id")].append(kid)
    terms = [[market, lang, " ".join(phrase), len(phrase), tf[market, lang, phrase], count, locales[market, lang]]
             for (market, lang, phrase), count in sorted(df.items(), key=lambda pair: (-pair[1], pair[0]))]
    intents = []
    for group in records(plan, "groups"):
        ids = intent_members[text(group, "id")]
        word = keywords[ids[0]]
        targets = sorted({str(decisions[k]["page_id"]) for k in ids if decisions[k]["page_id"]})
        intents.append({**group, "market": word["market"], "language": word["language"], "query_count": len(ids),
                        "representative": word["keyword_original"], "targets": targets})
    return {"terms": terms, "tokens": dict(zip(keywords, token_rows, strict=True)), "intents": intents,
            "sites": records(plan, "competitors"), "source_notes": records(plan, "sources"),
            "reviews": [{k: r.get(k, "") for k in ("competitor_id", "url", "status", "page_type", "summary",
                                                   "observed_at", "id")} for r in optional_records(plan, "competitor_page_reviews")]}


def save_term_table(path: Path, analysis: Row) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["market", "language", "term", "ngram", "tf", "df", "denominator_keywords"])
        for value in items(analysis["terms"]):
            writer.writerow(items(value))
