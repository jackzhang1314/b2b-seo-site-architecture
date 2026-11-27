"""Register a finished architecture delivery in its customer website workspace."""
from __future__ import annotations

import csv
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator
from urllib.parse import quote, unquote

from seo_project import (
    END, INDEX, KIND, START, WP_END, WP_START, block_range, identity, index_paths, load_index, local_file, project_for,
)

FILES = {"workbench": "index.html", "plan": "architecture-plan.json", "pages": "pages.csv",
         "library": "clean-keywords.csv", "decisions": "keyword-decisions.csv", "rankings": "received-rankings.csv",
         "top_pages": "page-traffic-observations.csv", "comparables": "comparable-pages.csv",
         "planning": "commercial-planning.csv", "intents": "intent-groups.csv", "frequency": "words-and-phrases.csv",
         "modules": "content-modules.csv", "fields": "design-fields.csv", "raw_catalog": "input-files.csv",
         "analysis": "analysis-manifest.json", "validation": "validation.json"}
LABELS = {"workbench": "工作台 HTML", "pages": "网站页面规划", "library": "清洗后的行业词库",
          "decisions": "逐词页面归属", "rankings": "竞品逐词排名", "top_pages": "竞品 Top Pages 数据",
          "raw_catalog": "全部原始资料清单"}


def atomic_write(path: Path, data: bytes, previous: bytes | None) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing a symlink as a project entry: {path}")
    if previous == data:
        return
    with NamedTemporaryFile(dir=path.parent, prefix=".seo-entry-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        if (path.read_bytes() if path.exists() else None) != previous:
            raise ValueError(f"{path.name} changed during registration; retry using --register-existing")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def project_lock(root: Path) -> Iterator[None]:
    """All SEO index writers lock the same persistent inode, including across processes."""
    if sys.platform == "win32":
        import msvcrt
        lock = root / ".seo-project-index.lock"
        if lock.is_symlink():
            raise ValueError("Invalid project lock path")
        with lock.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fd = os.open(root, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def inventory(directory: Path) -> dict[str, object]:
    project = json.loads(local_file(directory, FILES["plan"]).read_bytes())["project"]
    validation = json.loads(local_file(directory, FILES["validation"]).read_bytes())
    files = {role: name for role, name in FILES.items() if (directory / name).is_file()}
    for required in ("workbench", "pages", "decisions", "library", "raw_catalog"):
        if required not in files:
            raise ValueError(f"Delivery lacks {FILES[required]}. Compile a complete workbench first.")
    for name in files.values():
        local_file(directory, name)
    originals: list[dict[str, str]] = []
    with (directory / FILES["raw_catalog"]).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            item = {key: row.get(key, "") for key in ("family", "source_id", "kind", "name", "sha256")}
            relative = unquote(row.get("download", ""))
            if relative:
                local_file(directory, relative)
            originals.append({**item, "path": relative, "availability": "included" if relative else "catalog_only"})
    tables = sorted(p.name for p in directory.glob("*.csv") if p.is_file())
    for table in tables:
        local_file(directory, table)
    return {"kind": KIND, "schema_version": 1, **{k: project[k] for k in ("site_url", "market", "language", "mode")},
            "project_name": project.get("name", ""), "producer_version": validation.get("tool_version"),
            "research_complete": validation.get("research_complete"), "files": files,
            "csv_tables": tables, "originals": originals}


def render_entry(root: Path, index_path: Path, entry: dict[str, object]) -> str:
    folder = index_path.parent
    def link(label: str, path: Path) -> str:
        return f"[{label}](<{quote(path.relative_to(root).as_posix(), safe='/.-_~')}>)"
    # Use fixed labels; data values may include Markdown or control characters.
    scope = " · ".join(" ".join(str(entry[k]).split()) for k in ("site_url", "market", "language", "mode"))
    role_paths = entry["files"]
    if not isinstance(role_paths, dict):
        raise ValueError("Invalid SEO files inventory")
    rows = [f"### {scope}", "- SEO project index: " + link("架构与资料索引", index_path)]
    rows.append("- " + " · ".join(link(label, local_file(folder, role_paths[role]))
                                  for role, label in LABELS.items() if role in role_paths))
    originals = entry["originals"]
    if not isinstance(originals, list):
        raise ValueError("Invalid original data inventory")
    available = [r for r in originals if isinstance(r, dict) and r.get("path")]
    if available:
        # The full catalogue remains linked for large customer dossiers.
        by_kind: dict[str, Path] = {}
        for row in sorted(available, key=lambda item: item.get("family") != "supplied"):
            by_kind.setdefault(str(row.get("kind")), local_file(folder, str(row["path"])))
        roles = {"keywords": "词库原件", "rankings": "排名原表", "pages": "Top Pages 原表",
                 "competitors": "竞品名单", "material": "企业资料"}
        direct = [link(label, by_kind[k]) for k, label in roles.items() if k in by_kind]
        rows.append(f"- 已收原件 {len(available)} 份。" + (" 各类首份：" + " · ".join(direct) if direct else ""))
    else:
        rows.append("- 原件尚未附入，可从已有规划和清洗表继续。")
    return "\n".join(rows)


def register(root: Path, directory: Path) -> Path:
    root, directory = root.resolve(), directory.resolve()
    if (not root.is_dir() or directory == root or not directory.is_relative_to(root)
            or root == Path.home().resolve() or root == root.parent):
        raise ValueError("Register a workbench inside its own website project root")
    enclosing = project_for(directory.parent)
    if enclosing and enclosing != root and enclosing.is_relative_to(root):
        raise ValueError("The workbench belongs to a nested website project")
    key = directory / INDEX
    entry = inventory(directory)
    serialized = (json.dumps(entry, ensure_ascii=False, indent=2) + "\n").encode()
    with project_lock(root):
        agents = root / "AGENTS.md"
        if agents.is_symlink() or key.is_symlink():
            raise ValueError("Expected regular project entry and index files")
        original = agents.read_bytes() if agents.exists() else None
        body = (original or b"").decode("utf-8")
        bom = body.startswith("\ufeff")
        body = body.removeprefix("\ufeff")
        bounds = block_range(body)
        if WP_START in body.splitlines() or WP_END in body.splitlines():
            wp_start, wp_end = body.find(WP_START), body.find(WP_END)
            if wp_start < 0 or wp_end < wp_start or (bounds and bounds[0] < wp_end + len(WP_END) and wp_start < bounds[1]):
                raise ValueError("Overlapping or incomplete WordPress and SEO sections; preserve and separate them before registering")
        current = index_paths(root, body)
        entries: dict[tuple[str, ...], tuple[Path, dict[str, object]]] = {}
        for path in current:
            if path != key:
                item = load_index(path)
                entries[identity(item)] = (path, item)
        entries[identity(entry)] = (key, entry)
        sections = [render_entry(root, *entries[scope]) for scope in sorted(entries)]
        region = (START + "\n## SEO 网站架构与数据\n\n"
                  "做单页 SEO 或建站维护时，先按本站、市场和语言选择索引，读取本页归属、同行证据与原始资料。\n\n"
                  + "\n\n".join(sections) + "\n" + END)
        newline = "\r\n" if "\r\n" in body else "\n"
        region = region.replace("\n", newline)
        if bounds:
            result = body[:bounds[0]] + region + body[bounds[1]:]
        else:
            separator = "" if not body else newline if body.endswith("\n") else newline * 2
            result = body + separator + region + newline
        atomic_write(key, serialized, key.read_bytes() if key.exists() else None)
        atomic_write(agents, (("\ufeff" if bom else "") + result).encode(), original)
        if key not in index_paths(root, agents.read_text(encoding="utf-8-sig")):
            raise ValueError(f"AGENTS.md readback failed; retry --register-existing {directory}")
    return key
