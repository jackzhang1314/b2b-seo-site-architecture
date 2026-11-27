"""Read a website's registered workbench and maintain its per-revision Markdown summary."""
from __future__ import annotations

import json
import re
import unicodedata
from html import escape
from pathlib import Path
from urllib.parse import quote, unquote

from plan_records import Row, obj, records, text
from revisions import ROLES, read_revisions
from seo_project import INDEX, WP_END, WP_START, block_range, identity, local_file, project_for
from workbench_events import SCHEMA

HANDOFF = "workbench-handoff.json"
STATUS_START = "<!-- b2b-seo:page-status:start -->"
STATUS_END = "<!-- b2b-seo:page-status:end -->"
ROLE_TITLES = {"adopted": "当前采用", "draft": "新草案", "previous": "历史采用"}
FULL_PAGE_LIMIT = 24
LARGE_GROUP_LIMIT = 6


def managed_bounds(body: str) -> tuple[int, int] | None:
    bounds = block_range(body)
    wp_start = list(re.finditer("^" + re.escape(WP_START) + r"\r?$", body, re.M))
    wp_end = list(re.finditer("^" + re.escape(WP_END) + r"\r?$", body, re.M))
    if wp_start or wp_end:
        if (len(wp_start) != 1 or len(wp_end) != 1 or wp_end[0].start() < wp_start[0].end()
                or (bounds and bounds[0] < wp_end[0].end() and wp_start[0].start() < bounds[1])):
            raise ValueError("Overlapping or incomplete WordPress and SEO sections; repair the boundary markers")
    return bounds


def require_registered_owner(root: Path, folder: Path) -> None:
    if folder == root or not folder.is_relative_to(root) or project_for(folder) != root:
        raise ValueError("The registered workbench belongs to a different or nested website project")


def registered_handoff(folder: Path, entry: Row) -> Row:
    files = obj(entry["files"])
    remedy = (f"本版没有可用的逐页状态清单（{folder}）。请恢复该版 {HANDOFF} 后用 "
              "architecture.py --register-existing 重新登记，或明确指定已有逐页清单的工作台。")
    if files.get("handoff") != HANDOFF:
        raise ValueError(remedy)
    try:
        path = local_file(folder, HANDOFF)
    except ValueError as error:
        raise ValueError(remedy) from error
    if path != folder / HANDOFF:
        raise ValueError("Expected the handoff file in the registered workbench directory")
    handoff = obj(json.loads(path.read_bytes()))
    if handoff.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported workbench handoff: {path}")
    text(handoff, "revision_id")
    project = obj(handoff.get("project"))
    if not all(isinstance(project.get(k), str) for k in ("site_url", "market", "language", "mode")):
        raise ValueError(f"Missing site, market, language or mode in {path}")
    if identity(project) != identity(entry):
        raise ValueError(f"Workbench handoff and project index refer to different scopes: {path}")
    return handoff


def resolve_workbench(root: Path, *, site_url: str | None = None, market: str | None = None,
                      language: str | None = None, mode: str | None = None) -> tuple[Path, str]:
    """Select within one scope, without locking or changing the caller's project."""
    root = root.resolve()
    agents = root / "AGENTS.md"
    if agents.is_symlink() or not agents.is_file():
        raise ValueError("项目根目录缺少已登记的 AGENTS.md，请指定正确的 --project-root 或 --workbench。")
    body = agents.read_text(encoding="utf-8-sig")
    managed_bounds(body)
    filters = {k: v for k, v in (("site_url", site_url), ("market", market), ("language", language), ("mode", mode))
               if v is not None}
    matches = [(key, role, item) for key, (role, item) in read_revisions(root, body).items()
               if role != "previous" and identity(item) == identity({**item, **filters})]
    scopes = {identity(item) for _, _, item in matches}
    if not matches:
        raise ValueError("项目没有匹配的采用版或草案，请核对站点、市场、语言和模式，或指定 --workbench。")
    if len(scopes) != 1:
        raise ValueError("工作台范围不唯一，请用 --site-url、--market、--language、--mode 选定一个范围，或指定 --workbench。")
    adopted = [candidate for candidate in matches if candidate[1] == "adopted"]
    if not adopted and len(matches) != 1:
        raise ValueError("此范围有多个草案，请用 --workbench 明确选择一个版本。")
    key, role, entry = (adopted or matches)[0]
    require_registered_owner(root, key.parent)
    pages = records(registered_handoff(key.parent, entry), "pages")
    if role == "draft" and not any(p.get("decision") in ("build", "skip") or p.get("seo_decision") == "requested"
                                   for p in pages):
        raise ValueError("唯一草案尚无明确的建页选择或 SEO 排期，请在工作台保存选择，或指定 --workbench 查看该版。")
    return key.parent, role


def inline_text(value: str, *, limit: int = 90) -> str:
    """Keep page names literal on one Markdown line, including names copied from source data."""
    value = " ".join("".join(" " if unicodedata.category(c) in {"Cc", "Cf"} else c for c in value).split())
    if len(value) > limit:
        value = value[:limit - 1] + "…"
    # Intraword underscores (P_HOME) are already literal in Markdown and keep the ID searchable.
    return re.sub(r"([\\`*~\[\]()|!])|(?<!\w)_|_(?!\w)", lambda m: "\\" + m[0], escape(value, quote=False))


def heading(entry: Row, folder: Path, role: str) -> str:
    scope = [inline_text(text(entry, k)) for k in ("site_url", "market", "language", "mode")]
    return "### " + " · ".join([*scope, ROLE_TITLES[role], inline_text(folder.name)])


def project_link(root: Path, label: str, path: Path) -> str:
    return f"[{label}](<{quote(path.relative_to(root).as_posix(), safe='/.-_~')}>)"


def progress(page: Row, track: str) -> str | None:
    tracks = page.get("progress")
    lane = tracks.get(track) if isinstance(tracks, dict) else None
    status = lane.get("status") if isinstance(lane, dict) else None
    return status if isinstance(status, str) else None


def status_summary(root: Path, folder: Path, handoff: Row | None) -> str:
    if handoff is None:
        return "\n".join([STATUS_START, "- 本版未登记逐页状态清单。建页、SEO 排期和完成情况需另行核实。", STATUS_END])
    pages = records(handoff, "pages")
    revision = handoff.get("revision_id")
    limit = len(pages) if len(pages) <= FULL_PAGE_LIMIT else LARGE_GROUP_LIMIT
    rows = [STATUS_START]
    truncated = False

    def group(label: str, members: list[Row], *, indent: str = "", track: str = "") -> None:
        nonlocal truncated
        names: list[str] = []
        for page in members[:limit]:
            pid = text(page, "id")
            name = next((page[k] for k in ("name_zh", "name", "name_en") if page.get(k)), None)
            title = inline_text(pid)
            if isinstance(name, str) and name != pid:
                title = f"{inline_text(name)}（{title}）"
            if track:
                receipt = obj(obj(page.get("progress", {})).get(track, {}))
                origin = receipt.get("origin_revision_id")
                previous = bool(revision and origin and origin != revision)
                if receipt.get("status") == "published":
                    title += "〔前版已发布〕" if previous else "〔已发布〕"
                elif previous:
                    title += "〔前版回执〕"
            names.append(title)
        if len(members) > limit:
            truncated = True
            names.append(f"另 {len(members) - limit} 页")
        rows.append(f"{indent}- {label}（{len(members)} 页）" + ("　" + "、".join(names) if names else ""))

    for choice, label in (("build", "要建"), ("skip", "不建"), ("pending", "待定")):
        group(label, [p for p in pages if p.get("decision") == choice])
    if any("seo_decision" in p for p in pages):
        requested = [p for p in pages if p.get("seo_decision") == "requested"]
        rows.append(f"- 已安排 SEO（{len(requested)} 页）")
        if requested:
            group("可开始", [p for p in requested if p.get("seo_eligible") is True], indent="  ")
            group("等待条件", [p for p in requested if p.get("seo_eligible") is not True], indent="  ")
    for track, done, label in (("build", "built", "已建"), ("onpage", "optimized", "已优化")):
        if any(progress(p, track) is not None for p in pages):
            group(label, [p for p in pages if progress(p, track) in (done, "published")], track=track)
    if truncated:
        rows.append("- " + project_link(root, f"查看全部 {len(pages)} 页的选择、排期和完成记录", folder / HANDOFF))
    return "\n".join([*rows, STATUS_END])


def updated_status(body: str, root: Path, folder: Path, handoff: Row) -> str:
    """Splice only this revision's summary, keeping all other lines and line endings intact."""
    bounds = managed_bounds(body)
    if bounds is None:
        return body
    key = folder / INDEX
    match = read_revisions(root, body).get(key)
    if match is None:
        return body  # A standalone workbench can also live beneath a registered site.
    role, entry = match
    require_registered_owner(root, folder)
    saved = registered_handoff(folder, entry)
    if any(saved.get(field) != handoff.get(field) for field in ("revision_id", "change_number")):
        raise ValueError("此工作台的版本或逐页状态已变化，请重新读取后同步。")
    area = body[bounds[0]:bounds[1]]
    lines = area.splitlines(keepends=True)
    pointer = re.compile(r"^- " + ROLES[role] + r": \[[^\]\r\n]*\]\(<([^>\r\n]+)>\)[ \t]*$")
    index = next((i for i, line in enumerate(lines) if (link := pointer.fullmatch(line.rstrip("\r\n")))
                  and local_file(root, unquote(link[1])) == key), None)
    if index is None:
        raise ValueError("项目指针在同步期间发生变化，请重新读取 AGENTS.md 后重试。")
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    pointer_prefixes = tuple("- " + label + ":" for label in ROLES.values())
    previous = max((i for i in range(index) if lines[i].startswith(pointer_prefixes)), default=-1)
    end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith(("### ", *pointer_prefixes))), len(lines))
    segment = [line.rstrip("\r\n") for line in lines[index + 1:end]]
    start, stop = index + 1, index + 1
    if STATUS_START in segment or STATUS_END in segment:
        if (segment.count(STATUS_START) != 1 or segment.count(STATUS_END) != 1
                or segment.index(STATUS_START) >= segment.index(STATUS_END)):
            raise ValueError("该版本的逐页状态块不完整，请先修复 AGENTS.md 中的状态边界。")
        start += segment.index(STATUS_START)
        stop += segment.index(STATUS_END) + 1
    lines[start:stop] = [line + newline for line in status_summary(root, folder, saved).split("\n")]
    if not any(line.startswith("### ") for line in lines[previous + 1:index]):
        lines.insert(index, heading(entry, folder, role) + newline)
    return body[:bounds[0]] + "".join(lines) + body[bounds[1]:]
