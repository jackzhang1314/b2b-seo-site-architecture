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
    END, INDEX, KIND, START, WP_END, WP_START, block_range, identity, local_file, project_for,
)
from revisions import ROLES, RolePaths, catalogue, read_revisions

FILES = {"handoff": "workbench-handoff.json", "workbench": "index.html", "plan": "architecture-plan.json", "pages": "pages.csv",
         "library": "clean-keywords.csv", "decisions": "keyword-decisions.csv", "rankings": "received-rankings.csv",
         "top_pages": "page-traffic-observations.csv", "comparables": "comparable-pages.csv",
         "planning": "commercial-planning.csv", "intents": "intent-groups.csv", "frequency": "words-and-phrases.csv",
         "modules": "content-modules.csv", "fields": "design-fields.csv", "raw_catalog": "input-files.csv",
         "analysis": "analysis-manifest.json", "validation": "validation.json", "opportunities": "page-opportunities.csv",
         "design": "design.html", "templates": "templates.csv", "navigation": "navigation.csv",
         "design_modules": "design-modules.csv", "design_briefs": "design-briefs.md",
         "attributes": "product-attributes.csv", "design_handoff": "design-handoff.md"}
LABELS = {"handoff": "用户逐页确认清单", "workbench": "工作台 HTML", "pages": "网站页面规划", "library": "清洗后的行业词库",
          "decisions": "逐词页面归属", "rankings": "竞品逐词排名", "top_pages": "竞品 Top Pages 数据",
          "raw_catalog": "全部原始资料清单", "opportunities": "待核与已取舍的建页机会", "design": "建站设计简报"}


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


def render_entry(root: Path, index_path: Path, entry: dict[str, object], role: str) -> str:
    folder = index_path.parent
    def link(label: str, path: Path) -> str:
        return f"[{label}](<{quote(path.relative_to(root).as_posix(), safe='/.-_~')}>)"
    # Use fixed labels; data values may include Markdown or control characters.
    scope = " · ".join(" ".join(str(entry[k]).split()) for k in ("site_url", "market", "language", "mode"))
    role_paths = entry["files"]
    if not isinstance(role_paths, dict):
        raise ValueError("Invalid SEO files inventory")
    label = {"adopted": "当前采用", "draft": "新草案", "previous": "历史采用"}[role]
    rows = [f"### {scope} · {label}", "- " + ROLES[role] + ": " + link(label + "的架构索引", index_path)]
    if role != "adopted":
        rows.append("- " + link(label + "的工作台", local_file(folder, role_paths["workbench"])))
        if "handoff" in role_paths:
            rows.append("- " + link(label + "的用户逐页确认清单", local_file(folder, role_paths["handoff"])))
        return "\n".join(rows)
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


def register(root: Path, directory: Path, *, adopt: bool = False) -> Path:
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
        entries: RolePaths = read_revisions(root, body)
        scope = identity(entry)
        if key in entries and identity(entries[key][1]) != scope:
            raise ValueError("A registered revision cannot change site, market, language or mode")
        role = entries[key][0] if key in entries else "draft"
        if adopt:
            for path, (prior_role, item) in list(entries.items()):
                if path != key and prior_role == "adopted" and identity(item) == scope:
                    entries[path] = ("previous", item)
            role = "adopted"
        entries[key] = (role, entry)
        priority = {"adopted": 0, "draft": 1, "previous": 2}
        order = sorted(entries, key=lambda p: (identity(entries[p][1]), priority[entries[p][0]], p))
        sections = [render_entry(root, path, entries[path][1], entries[path][0]) for path in order]
        region = (START + "\n## SEO 网站架构与数据\n\n"
                  "建站和日常 SEO 读取对应站点、市场及语言的「当前采用」索引。只有草案时，实施方案尚待选定。"
                  "用户给出具体工作台链接时，按链接读取该版最新的用户逐页确认清单。build 纳入本次工作，skip 跳过，pending 待定。"
                  "用页面编号关联本版的关键词、模块和建站模板。此选择独立于采用版登记。旧版缺少逐页选择时向用户核实范围。"
                  "本机链接暂停时按其路径找到本项目内的原工作台，用架构 Skill 的 scripts/workbench.py --workbench 重开。"
                  "On-page 的 --source 指向所选版目录。\n\n"
                  + "\n\n".join(sections) + "\n" + END)
        newline = "\r\n" if "\r\n" in body else "\n"
        region = region.replace("\n", newline)
        if bounds:
            result = body[:bounds[0]] + region + body[bounds[1]:]
        else:
            separator = "" if not body else newline if body.endswith("\n") else newline * 2
            result = body + separator + region + newline
        catalog_path, catalog_data, context_data = catalogue(root, key, entries)
        updates = [(key, serialized)]
        # Only new workbenches use the local version selector. Older HTML remains byte-identical.
        context_path = directory / "workbench-context.js"
        if context_path.is_file():
            updates.append((context_path, context_data))
        updates += [(catalog_path, catalog_data), (agents, (("\ufeff" if bom else "") + result).encode())]
        written: list[tuple[Path, bytes, bytes | None]] = []
        try:
            for target, content in updates:
                previous = original if target == agents else target.read_bytes() if target.exists() else None
                atomic_write(target, content, previous)
                if content != previous:
                    written.append((target, content, previous))
            if read_revisions(root, agents.read_text(encoding="utf-8-sig"))[key][0] != role:
                raise ValueError("Architecture revision readback failed")
        except (OSError, ValueError, KeyError):
            for target, content, prior in reversed(written):
                if target.read_bytes() == content:
                    if prior is None:
                        target.unlink()
                    else:
                        atomic_write(target, prior, content)
            raise
    return key
