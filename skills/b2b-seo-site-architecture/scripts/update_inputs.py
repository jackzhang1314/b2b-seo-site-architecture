"""Recover portable intake manifests from a previous delivery's verified originals."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import unquote

from delivery import digest
from library import items, output_dir, write_json
from plan_records import Row, obj, records, require, text
from revisions import read_delivery_rows
from seo_project import local_file

INTAKE_METADATA = {"resolved_path", "received_file", "records", "source_ref_rule", "sha256", "path"}


def input_rows(folder: Path) -> list[dict[str, str]]:
    return list(read_delivery_rows(local_file(folder, "input-files.csv")))


def registered_sources(previous: Path) -> dict[tuple[str, str], Row]:
    mappings: dict[tuple[str, str], Row] = {}
    analysis = obj(json.loads(local_file(previous, "analysis-manifest.json").read_bytes()))
    for run in records(analysis, "runs"):
        family = text(run, "command")
        if family not in {"build", "rankings"}:
            continue
        register = local_file(previous, text(run, "directory") + "/source-register.json")
        for entry in items(json.loads(register.read_bytes())):
            source = obj(entry)
            key = family, text(source, "source_id")
            require(key not in mappings, f"Duplicate registered source: {key}")
            mappings[key] = source
    return mappings


def linked_inputs(previous: Path) -> tuple[dict[str, list[Row]], list[Row]]:
    """Recover the received file, never a stale absolute path on the original author's computer."""
    result: dict[str, list[Row]] = {"build": [], "rankings": [], "supplied": []}
    mappings = registered_sources(previous)
    unavailable: list[Row] = []
    for row in input_rows(previous):
        family, sid = row["family"], row["source_id"]
        if not row["download"]:
            known = mappings.get((family, sid), {})
            unavailable.append({"family": family, "source_id": sid, "name": row["name"], "sha256": row["sha256"],
                                "mapping": {k: v for k, v in known.items() if k not in INTAKE_METADATA}})
            continue
        path = local_file(previous, unquote(row["download"]))
        require(digest(path) == row["sha256"], f"Archived original differs from intake: {family}/{sid}")
        if family == "supplied":
            data: Row = {k: row[k] for k in ("source_id", "kind", "label", "provider", "observed_at", "evidence_class")
                         if row.get(k)}
        else:
            saved_source = mappings.get((family, sid))
            if saved_source is None or saved_source.get("sha256") != row["sha256"]:
                raise ValueError(f"Missing or mismatched original column mapping: {family}/{sid}")
            data = {k: v for k, v in saved_source.items() if k not in INTAKE_METADATA}
        data["path"] = str(path)
        result[family].append(data)
    return result, unavailable


def combined_sources(archived: list[Row], additions: Path | None, key: str, relative_to: Path) -> list[Row]:
    """Append new observations while making repeated preparation of the same export idempotent."""
    relative_to = relative_to.resolve()
    added = [] if additions is None else records(obj(json.loads(additions.read_bytes())), key)
    by_id: dict[str, Row] = {}
    for n, source in enumerate([*archived, *added], 1):
        entry = dict(source)
        path = Path(text(entry, "path"))
        if n > len(archived) and additions is not None:
            path = additions.parent / path
        path = path.resolve()
        require(path.is_file(), f"Missing original: {path}")
        if key == "files" and not entry.get("source_id"):
            generated = n
            while f"file_{generated}" in by_id:
                generated += 1
            entry["source_id"] = f"file_{generated}"
        if key == "files":
            for field, default in (("label", path.name), ("kind", "material"), ("provider", ""),
                                   ("observed_at", ""), ("evidence_class", "user_provided")):
                entry.setdefault(field, default)
        sid = text(entry, "source_id")
        entry["path"] = Path(os.path.relpath(path, relative_to)).as_posix()
        prior = by_id.get(sid)
        if prior is not None:
            same = digest((relative_to / text(prior, "path")).resolve()) == digest(path)
            # Reusing a source ID with a different date, market or mapping would rewrite old evidence.
            metadata = set(prior) | set(entry)
            same = same and all(prior.get(k) == entry.get(k) for k in metadata if k != "path")
            require(same, f"Source ID {sid} already identifies another export or mapping; give the addition a new source_id")
        else:
            by_id[sid] = entry
    return list(by_id.values())


def prepare_intake(previous: Path, out: Path, keyword_sources: Path | None = None,
                   ranking_sources: Path | None = None, materials: Path | None = None) -> Row:
    previous, out = previous.resolve(), out.resolve()
    require(not out.is_relative_to(previous), "Choose a new intake directory outside the previous workbench")
    sources, unavailable = linked_inputs(previous)
    input_kinds = (("build", "keyword-sources.json", "sources", keyword_sources),
                   ("rankings", "ranking-sources.json", "sources", ranking_sources),
                   ("supplied", "materials.json", "files", materials))
    counts: dict[str, int] = {}
    with output_dir(out) as stage:
        for family, filename, key, additions in input_kinds:
            entries = combined_sources(sources[family], additions, key, out)
            write_json(stage / filename, {key: entries})
            counts[family] = len(entries)
        report: Row = {"sources": counts, "catalog_only": unavailable}
        write_json(stage / "intake-status.json", report)
    return report


def preserve_received(previous: Path, current: Path) -> Row:
    """An incremental delivery keeps earlier raw exports and query demand even if pages are dropped."""
    def originals(folder: Path) -> set[tuple[str, str, str]]:
        return {(r["family"], r["source_id"], r["sha256"]) for r in input_rows(folder) if r["download"]}
    old_sources, current_sources = originals(previous), originals(current)
    missing = sorted(sid for _, sid, _ in old_sources - current_sources)
    require(not missing, f"Previous originals missing or replaced: {', '.join(missing[:10])}. "
            "Use --prepare-update and rebuild with both old and new exports.")
    current_register = registered_sources(current)
    for key, before in registered_sources(previous).items():
        after = current_register.get(key)
        if after is None:
            raise ValueError(f"Earlier observations from {key} are missing. Restore this original using its saved mapping.")
        # Original bytes and their market, language, dates and column meanings identify one observation source.
        content = set(before) | set(after)
        require(before.get("sha256") == after.get("sha256")
                and all(before.get(k) == after.get(k) for k in content - INTAKE_METADATA),
                f"Earlier source {key} changed its data or column mapping. Keep it and assign a new source_id to the addition.")
    def queries(folder: Path) -> set[tuple[str, str, str]]:
        return {(r["market"].casefold(), r["language"].casefold(), r["keyword_normalized"])
                for r in read_delivery_rows(local_file(folder, "clean-keywords.csv"))}
    old_queries, current_queries = queries(previous), queries(current)
    removed = old_queries - current_queries
    require(not removed, f"The updated library is missing {len(removed)} prior queries. "
            "Rebuild the library from both the previous and newly received sources.")
    return {"previous_queries": len(old_queries), "current_queries": len(current_queries),
            "added_queries": len(current_queries - old_queries),
            "added_sources": [{"family": family, "source_id": sid, "sha256": sha}
                              for family, sid, sha in sorted(current_sources - old_sources)]}
