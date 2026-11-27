"""Portable revision catalogue. The website's managed AGENTS section owns version selection."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

from seo_project import block_range, identity, load_index, local_file

ROLES = {"adopted": "SEO project index", "draft": "SEO draft index", "previous": "SEO previous index"}
RolePaths = dict[Path, tuple[str, dict[str, object]]]


def read_revisions(root: Path, body: str) -> RolePaths:
    bounds = block_range(body)
    if bounds is None:
        return {}
    area = body[bounds[0]:bounds[1]]
    result: RolePaths = {}
    active: set[tuple[str, ...]] = set()
    for role, label in ROLES.items():
        matches = re.findall(r"^- " + label + r": \[[^\]\r\n]*\]\(<([^>\r\n]+)>\)[ \t]*\r?$", area, re.M)
        if len(matches) != sum(line.startswith("- " + label + ":") for line in area.splitlines()):
            raise ValueError(f"Invalid {label} in project entry")
        for rel in matches:
            path = local_file(root, unquote(rel))
            item = load_index(path)
            scope = identity(item)
            if path in result or (role == "adopted" and scope in active):
                raise ValueError("Ambiguous architecture revision in project entry")
            if role == "adopted":
                active.add(scope)
            result[path] = (role, item)
    if not result:
        raise ValueError("No valid architecture revisions in managed project section")
    return result


def page_signatures(folder: Path) -> dict[str, tuple[str, str]]:
    """Compare the procurement brief, template assignment, editable modules and word ownership."""
    parts: dict[str, list[str]] = defaultdict(list)
    names: dict[str, str] = {}
    tables = [
        ("pages.csv", "id", ("name", "url", "parent_id", "page_type", "template_id", "variant", "objective", "approval",
                             "primary_keyword", "sections", "fact_gaps", "rationale", "boundary", "page_action", "priority",
                             "design_prompt", "search_scope", "unique_content", "state", "cta", "offering_ids", "evidence_ids")),
        ("content-modules.csv", "page_id", ("module_id", "order", "buyer_question", "content", "required_facts")),
        ("keyword-decisions.csv", "page_id", ("keyword_id", "group_id", "role", "module_id", "disposition", "coverage", "reason")),
    ]
    for file, owner, fields in tables:
        if not (folder / file).exists():
            continue
        with local_file(folder, file).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                pid = row.get(owner, "")
                if not pid:
                    continue
                if owner == "id":
                    names[pid] = row.get("name", pid)
                parts[pid].append(json.dumps([file, *[row.get(f, "") for f in fields]], ensure_ascii=False))
    return {pid: (name, hashlib.sha256("\n".join(sorted(parts[pid])).encode()).hexdigest())
            for pid, name in names.items()}


def opportunity_signatures(folder: Path) -> dict[str, tuple[str, str, str]] | None:
    filename = "page-opportunities.csv"
    if not (folder / filename).exists():
        return None
    with local_file(folder, filename).open(encoding="utf-8-sig", newline="") as stream:
        return {row["id"]: (row["title"], row["status"], json.dumps(row, sort_keys=True)) for row in csv.DictReader(stream)}


def opportunity_changes(folder: Path, baseline: dict[str, tuple[str, str, str]] | None) -> list[dict[str, str]] | None:
    current = opportunity_signatures(folder)
    if baseline is None or current is None:
        return None
    result = []
    for ident in sorted(baseline.keys() | current.keys()):
        previous, item = baseline.get(ident), current.get(ident)
        if previous != item:
            label = current[ident][0] if item else baseline[ident][0]
            result.append({"id": ident, "name": label, "before": previous[1] if previous else "",
                           "after": item[1] if item else ""})
    return result


def catalogue(root: Path, key: Path, entries: RolePaths) -> tuple[Path, bytes, bytes]:
    scope = identity(entries[key][1])
    releases = {p: role for p, (role, item) in entries.items() if identity(item) == scope}
    adopted = next((p for p, role in releases.items() if role == "adopted"), None)
    baseline = page_signatures(adopted.parent) if adopted else {}
    prior_opportunities = opportunity_signatures(adopted.parent) if adopted else None
    versions: list[dict[str, object]] = []
    for p, role in releases.items():
        relative = p.parent.relative_to(root).as_posix()
        differences: list[dict[str, str]] = []
        if adopted and p != adopted:
            pages = page_signatures(p.parent)
            for pid in sorted(baseline.keys() | pages.keys()):
                if pages.get(pid) == baseline.get(pid):
                    continue
                action = "added" if pid not in baseline else "removed" if pid not in pages else "changed"
                differences.append({"id": pid, "name": (pages[pid] if pid in pages else baseline[pid])[0], "action": action})
        versions.append({"directory": relative, "name": p.parent.name, "role": role, "changes": differences,
                         "opportunity_changes": opportunity_changes(p.parent, prior_opportunities) if adopted and p != adopted else None,
                         "href": quote(relative + "/index.html", safe="/.-_~")})
    slug = hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:24]
    directory = root / ".seo-architecture"
    if directory.is_symlink():
        raise ValueError("Invalid architecture catalogue folder")
    directory.mkdir(exist_ok=True)
    path = directory / (slug + ".js")
    here = key.parent.relative_to(root).as_posix()
    context = {"directory": here, "root": os.path.relpath(root, key.parent).replace(os.sep, "/") + "/",
               "catalogue": quote(os.path.relpath(path, key.parent).replace(os.sep, "/"), safe="/.-_~")}
    def script(name: str, value: object) -> bytes:
        return ("window." + name + " = " + json.dumps(value, ensure_ascii=True, separators=(",", ":")) + ";\n").encode()
    return path, script("architectureVersions", versions), script("architectureContext", context)
