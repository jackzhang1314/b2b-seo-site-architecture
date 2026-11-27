"""Read the portable website project's SEO pointers (shared by both standalone Skills)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

START = "<!-- b2b-seo:architecture:start -->"
END = "<!-- b2b-seo:architecture:end -->"
WP_START = "<!-- wordpress-builder:project:start -->"
WP_END = "<!-- wordpress-builder:project:end -->"
INDEX = "seo-project-index.json"
KIND = "b2b-seo-site-architecture"
LINK = re.compile(r"^- SEO project index: \[[^\]\r\n]*\]\(<([^>\r\n]+)>\)[ \t]*\r?$", re.MULTILINE)


def local_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Expected a project-relative file: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path points outside the project: {relative}")
    return resolved


def local_file(root: Path, relative: str) -> Path:
    path = local_path(root, relative)
    if not path.is_file():
        raise ValueError(f"Missing project file: {relative}")
    return path


def project_for(path: Path) -> Path | None:
    """Locate the closest website entry without crossing an unrelated repository."""
    path = path.resolve()
    for root in (path, *path.parents):
        agents = root / "AGENTS.md"
        if agents.is_file() and not agents.is_symlink():
            body = agents.read_text(encoding="utf-8-sig")
            lines = body.splitlines()
            if (any(marker in lines for marker in (START, WP_START, "<!-- wordpress-builder:workspace -->"))
                    or any(line.startswith("- Shared Builder CLI:") for line in lines)):
                return root
        config = root / "project.json"
        if config.is_file() and not config.is_symlink():
            try:
                metadata = json.loads(config.read_bytes())
            except (ValueError, UnicodeError):
                metadata = None
            if isinstance(metadata, dict) and metadata.get("type") == "wordpress-b2b":
                return root
        if (root / ".git").exists():
            break
    return None


def block_range(body: str) -> tuple[int, int] | None:
    starts = list(re.finditer("^" + re.escape(START) + r"\r?$", body, re.M))
    ends = list(re.finditer("^" + re.escape(END) + r"\r?$", body, re.M))
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0].end() > ends[0].start():
        raise ValueError("Malformed SEO section in AGENTS.md; repair its boundary markers before updating")
    return starts[0].start(), ends[0].end() - int(ends[0].group().endswith("\r"))


def index_paths(root: Path, body: str) -> list[Path]:
    bounds = block_range(body)
    if bounds is None:
        return []
    area = body[bounds[0]:bounds[1]]
    links = LINK.findall(area)
    if not links or len(links) != sum(line.startswith("- SEO project index:") for line in area.splitlines()):
        raise ValueError("SEO project index link missing or invalid in AGENTS.md")
    return [local_file(root, unquote(link)) for link in links]


def load_index(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (path.name != INDEX or not isinstance(data, dict) or data.get("kind") != KIND
            or data.get("schema_version") != 1):
        raise ValueError(f"Unsupported website SEO project index: {path}")
    for key in ("site_url", "market", "language", "mode"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError(f"Missing {key} in {path}")
    if not isinstance(data.get("files"), dict) or not isinstance(data.get("originals"), list):
        raise ValueError(f"Invalid data inventory in {path}")
    return data


def identity(entry: dict[str, object]) -> tuple[str, str, str, str, str]:
    url = urlsplit(str(entry["site_url"]))
    return (url.netloc.casefold(), url.path.rstrip("/"), str(entry["market"]).casefold(),
            str(entry["language"]).casefold(), str(entry["mode"]))
