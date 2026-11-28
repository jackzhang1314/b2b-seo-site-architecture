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
from demand_metrics import totals
from library import items, read_table, stable_id, text_map
from plan_records import Row, obj, optional_records, records, require, strings, text

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


Term = tuple[str, str, tuple[str, ...]]
TERM_METRICS = ("known_monthly_volume", "volume_coverage", "volume_status", "kd_coverage", "kd_status",
                "kd_under_30", "kd_30_to_under_60", "kd_60_to_100", "kd_unknown")


def term_members(keywords: dict[str, dict[str, str]], tokens: dict[str, list[str]], lengths: Iterable[int],
                 requested: set[Term] | None = None) -> tuple[Counter[Term], dict[Term, list[str]]]:
    counts: Counter[Term] = Counter()
    members: dict[Term, list[str]] = defaultdict(list)
    for kid, word in keywords.items():
        parts = tokens[kid]
        seen: set[Term] = set()
        for length in lengths:
            for i in range(len(parts) - length + 1):
                key = (word["market"], word["language"], tuple(parts[i:i+length]))
                if requested is not None and key not in requested:
                    continue
                counts[key] += 1
                if key not in seen:
                    members[key].append(kid)
                    seen.add(key)
    return counts, members


def verify_frequency(analysis: Row, keywords: dict[str, dict[str, str]]) -> None:
    tokenizer = analysis.get("tokenizer", "standard")
    require(tokenizer in {"standard", "custom"}, "Use standard or custom keyword_analysis.tokenizer")
    if tokenizer == "custom":
        text(analysis, "tokenizer_description")
    tokens: dict[str, list[str]] = {}
    for row in records(analysis, "tokenized_keywords"):
        kid = text(row, "keyword_id")
        require(kid in keywords and kid not in tokens, "Tokenized keyword coverage differs from input")
        require(text(row, "keyword") == keywords[kid]["keyword_original"], "Tokenized original keyword differs")
        parts = strings(row, "tokens", unique=False)
        require(tokenizer == "custom" or parts == TOKEN.findall(keywords[kid]["keyword_normalized"]),
                f"Tokenized keyword {kid} differs from original; document an alternative tokenizer explicitly")
        tokens[kid] = parts
    require(tokens.keys() == keywords.keys(), "Tokenized keyword coverage differs from input")
    wanted: dict[Term, Row] = {}
    for row in records(analysis, "terms"):
        phrase = tuple(text(row, "term").split(" "))
        market, language = text(row, "market"), text(row, "language", empty=True)
        for field in ("tf", "df", "denominator_keywords", "ngram"):
            amount = row.get(field)
            require(type(amount) is int and amount > 0, f"Invalid word-frequency {field}")
        key = market, language, phrase
        require(key not in wanted and row["ngram"] == len(phrase) and all(phrase),
                f"Duplicate or inconsistent word-frequency ngram: {row['term']}")
        wanted[key] = row
    counts, members = term_members(keywords, tokens, {len(key[2]) for key in wanted}, set(wanted))
    denominators = Counter((word["market"], word["language"]) for word in keywords.values())
    for key, row in wanted.items():
        listed = set(strings(row, "keyword_ids", required=True))
        require(row["denominator_keywords"] == denominators[key[0], key[1]] and row["tf"] == counts[key]
                and row["df"] == len(members[key]) and listed == set(members[key]),
                f"Word-frequency TF/DF coverage mismatch for {row['term']} in {key[0]}/{key[1]}: "
                f"source tokens give TF={counts[key]}, DF={len(members[key])}, denominator={denominators[key[0], key[1]]}")


def keyword_analysis(plan: Row, keywords: dict[str, dict[str, str]], decisions: dict[str, Row]) -> Row:
    tokens = {kid: TOKEN.findall(word["keyword_normalized"]) for kid, word in keywords.items()}
    locales = Counter((word["market"], word["language"]) for word in keywords.values())
    tf, members = term_members(keywords, tokens, range(1, 4))
    intent_members: dict[str, list[str]] = defaultdict(list)
    for kid in keywords:
        intent_members[text(decisions[kid], "group_id")].append(kid)
    terms: list[list[object]] = []
    for key, ids in sorted(members.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        market, lang, phrase = key
        aggregate = totals(ids, decisions)
        dims = sorted({dim for kid in ids if "dimension_value_ids" in decisions[kid]
                       for dim in strings(decisions[kid], "dimension_value_ids")})
        terms.append([market, lang, " ".join(phrase), len(phrase), tf[key], len(ids), locales[market, lang],
                      *(aggregate[field] for field in TERM_METRICS), dims])
    intents = []
    for group in records(plan, "groups"):
        ids = intent_members[text(group, "id")]
        word = keywords[ids[0]]
        targets = sorted({str(decisions[k]["page_id"]) for k in ids if decisions[k]["page_id"]})
        intents.append({**group, "market": word["market"], "language": word["language"], "query_count": len(ids),
                        "representative": word["keyword_original"], "targets": targets})
    return {"terms": terms, "tokens": tokens, "intents": intents,
            "sites": records(plan, "competitors"), "source_notes": records(plan, "sources"),
            "reviews": [{k: r.get(k, "") for k in ("competitor_id", "url", "status", "page_type", "summary",
                                                   "observed_at", "id")} for r in optional_records(plan, "competitor_page_reviews")]}


def save_term_table(path: Path, analysis: Row) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["market", "language", "term", "ngram", "tf", "df", "denominator_keywords",
                         *TERM_METRICS, "dimension_value_ids"])
        for value in items(analysis["terms"]):
            cells = items(value)
            writer.writerow([*cells[:-1], json.dumps(cells[-1], ensure_ascii=False)])
