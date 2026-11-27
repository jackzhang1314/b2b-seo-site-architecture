"""Portable revision catalogue. The website's managed AGENTS section owns version selection."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from threading import Lock
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from seo_project import block_range, identity, load_index, local_file

ROLES = {"adopted": "SEO project index", "draft": "SEO draft index", "previous": "SEO previous index"}
RolePaths = dict[Path, tuple[str, dict[str, object]]]
_CSV_LIMIT_LOCK = Lock()


def read_delivery_rows(path: Path) -> Iterator[dict[str, str]]:
    """Generated cells can contain every query assigned to a page, including tens of thousands of terms."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        size = os.fstat(stream.fileno()).st_size
        # csv's ceiling is interpreter-wide. Raise it only as needed and never lower it under another reader.
        with _CSV_LIMIT_LOCK:
            if size > csv.field_size_limit():
                try:
                    csv.field_size_limit(size)
                except OverflowError:  # Python versions using a 32-bit C long on Windows.
                    csv.field_size_limit(2**31 - 1)
        yield from csv.DictReader(stream)


def normalized_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or "\\" in value
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError(f"Invalid planned page URL: {value}")
    host = parts.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if parts.port is not None and parts.port != {"http": 80, "https": 443}[parts.scheme]:
        host += ":" + str(parts.port)
    # Preserve path case, a non-root trailing slash, reserved escapes and the protocol.
    path = parts.path or "/"
    if re.search(r"%(?![\da-fA-F]{2})", path):
        raise ValueError(f"Invalid percent encoding in planned URL: {value}")
    def normalize_escape(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if re.fullmatch(r"[A-Za-z0-9._~-]", char) else match.group().upper()
    path = re.sub(r"%([\da-fA-F]{2})", normalize_escape, path)
    return urlunsplit((parts.scheme, host, path, "", ""))


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
        ("pages.csv", "id", ("name", "name_en", "name_zh", "url", "parent_id", "page_type", "template_id", "variant",
                             "objective", "approval", "primary_keyword", "sections", "sections_zh", "fact_gaps", "rationale",
                             "boundary", "page_action", "priority", "priority_reason", "adjacent_page_ids", "price_refs",
                             "design_prompt", "unique_content", "state", "cta", "cta_zh", "offering_ids", "evidence_ids")),
        ("content-modules.csv", "page_id", ("module_id", "order", "name", "buyer_question", "content", "required_facts")),
        ("keyword-decisions.csv", "page_id", ("keyword_id", "keyword", "market", "language", "group_id", "group_label",
                                              "buyer_task", "role", "module_id", "disposition", "coverage", "remaining_need",
                                              "dimension_value_ids", "reason")),
        ("comparable-pages.csv", "page_id", ("url", "page_type", "status", "reason")),
    ]
    for file, owner, fields in tables:
        if not (folder / file).exists():
            continue
        for row in read_delivery_rows(local_file(folder, file)):
            pid = row.get(owner, "")
            if not pid:
                continue
            if owner == "id":
                names[pid] = row.get("name", pid)
                if row.get("url"):
                    row["url"] = normalized_url(row["url"])
            for key in ("offering_ids", "evidence_ids", "adjacent_page_ids", "price_refs", "dimension_value_ids"):
                if row.get(key):
                    row[key] = "\n".join(sorted(row[key].splitlines()))
            payload = json.dumps([file, *[row.get(f, "") for f in fields]], ensure_ascii=False)
            parts[pid].append(hashlib.sha256(payload.encode()).hexdigest())
    return {pid: (name, hashlib.sha256("\n".join(sorted(parts[pid])).encode()).hexdigest())
            for pid, name in names.items()}


def opportunity_signatures(folder: Path) -> dict[str, tuple[str, str, str]] | None:
    filename = "page-opportunities.csv"
    if not (folder / filename).exists():
        return None
    return {row["id"]: (row["title"], row["status"], json.dumps(row, sort_keys=True))
            for row in read_delivery_rows(local_file(folder, filename))}


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
