"""Upgrade a published 0.8 or 0.9 editor in place while preserving compiled research and page choices."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from page_confirmation import FILENAME, encode, populate_planning_context
from plan_records import Row, obj, records, require, text
from register_project import atomic_write, sync_entry_locked
from revisions import keyword_changes
from search_plan import workbench_template
from seo_project import local_file, project_for
from workbench_events import normalize_handoff
from workbench_update import checked_handoff, project_scope, workbench_lock

# Fingerprints of the distributed 0.8.0 templates after assembly, excluding revision and embedded research.
VIEWS = {
    "index.html": ("search-plan.html", "a67e3fead07826f938bbe43cb6192741f7b670329ca2fdc1ded294c14595f531"),
    "design.html": ("architecture-view.html", "013562e9bb829e7e47ee6c063bfe04d11cbc1fa25647c829a4ffa01511604006"),
}
V09_SHELLS = {"index.html": "6ec31f09980e3c0ecaf868b3821a2ae0424571baed7a2d2b4121d724199ba279",
              "design.html": "afd3695d865cce701d9bbc6c94c1f6211696ad6321e4323c6548dc45bef4669b"}
DATA = re.compile(r'(<script type="application/json" id="(architecture-data|search-data)">)(.*?)(</script>)', re.S)
TOKENS = {"architecture-data": "__ARCHITECTURE_DATA__", "search-data": "__SEARCH_PLAN__"}
REVISION = '<meta name="workbench-revision" content="__WORKBENCH_REVISION__">'


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def updated_view(stored: bytes, template: str, name: str, data: Row) -> bytes:
    revision = str(UUID(text(data, "revision_id")))
    marker = REVISION.replace("__WORKBENCH_REVISION__", revision)
    html = stored.decode("utf-8")
    require(html.count(marker) == 1 and template.count(REVISION) == 1, f"{name}: revision marker differs from handoff")
    payload: dict[str, str] = {}

    def extract(match: re.Match[str]) -> str:
        start, key, value, end = match.groups()
        require(key not in payload and "<" not in value, f"{name}: ambiguous saved {key}")
        content = obj(json.loads(value))
        require(project_scope(obj(content["project"])) == project_scope(obj(data["project"])),
                f"{name}: embedded research belongs to another project")
        payload[key] = value
        return start + TOKENS[key] + end

    shell = DATA.sub(extract, html.replace(marker, REVISION, 1)).replace("\r\n", "\n")
    require(set(payload) == (set(TOKENS) if name == "index.html" else {"architecture-data"}),
            f"{name}: the required original research data is missing")
    require(sha(shell.encode()) in {VIEWS[name][1], V09_SHELLS[name]} or shell == template,
            f"{name}: unrecognized or customized HTML. Keep the original and compile a separate revision.")
    def render(match: re.Match[str]) -> str:
        start, key, token, end = match.groups()
        require(token == TOKENS[key] and key in payload, f"Unexpected data placeholder in {name}")
        return start + payload[key] + end
    return DATA.sub(render, template.replace(REVISION, marker, 1)).encode()


def preserve_backup(folder: Path, name: str, originals: dict[str, bytes]) -> Path:
    backup = folder / name
    require(not backup.is_symlink(), "Invalid workbench upgrade backup path")
    if backup.exists():
        manifest = obj(json.loads(local_file(backup, "sha256.json").read_bytes()))
        for filename in originals:
            require(sha(local_file(backup, filename).read_bytes()) == manifest.get(filename),
                    f"Original upgrade backup failed its integrity check: {filename}")
        return backup
    with TemporaryDirectory(prefix=".upgrade-", dir=folder) as temp:
        stage = Path(temp) / "backup"
        stage.mkdir()
        for filename, content in originals.items():
            (stage / filename).write_bytes(content)
        (stage / "sha256.json").write_bytes(encode({k: sha(v) for k, v in originals.items()}))
        stage.rename(backup)
    return backup


def upgrade_workbench(folder: Path, target_version: str, project_root: Path | None = None) -> Row:
    folder = folder.resolve()
    if project_root is not None:
        require(folder.is_relative_to(project_root.resolve()), "The selected editor is outside this website project")
    assets = Path(__file__).resolve().parents[1] / "assets"
    with workbench_lock(folder, project_root):
        validation = obj(json.loads(local_file(folder, "validation.json").read_bytes()))
        version = validation.get("tool_version")
        require(version in {"0.8.0", "0.9.0", target_version},
                f"In-place upgrade supports the original 0.8.0 and 0.9.0 workbenches; this delivery was compiled with {version!r}. "
                "Keep it intact and compile a separate workbench from its plan and library.")
        originals = {k: local_file(folder, k).read_bytes() for k in (*VIEWS, FILENAME)}
        data = checked_handoff(folder, originals[FILENAME])
        updated = {k: updated_view(originals[k], workbench_template(assets / asset), k, data)
                   for k, (asset, _) in VIEWS.items()}
        prior_upgrade = obj(data.get("editor_upgrade", {}))
        if version == target_version or prior_upgrade.get("version") == target_version:
            require(all(originals[k] == content for k, content in updated.items()),
                    "The saved editor version differs from its HTML. Restore the verified editor files before retrying.")
            if prior_upgrade:
                name = f".workbench-originals-{prior_upgrade.get('from_version', version)}-{data['revision_id']}"
                require(prior_upgrade.get("backup") == name and (folder / name).is_dir(),
                        "Restore the recorded original editor backup to complete the audit readback")
                preserve_backup(folder, name, originals)
            return {"status": "already_current", "revision_id": data["revision_id"], "version": target_version,
                    "backup": prior_upgrade.get("backup")}
        # The same fixed backup supports a retry after an interrupted in-place upgrade.
        from_version = prior_upgrade.get("version", version)
        require(from_version in {"0.8.0", "0.9.0"}, "Unrecognized saved editor version")
        backup = preserve_backup(folder, f".workbench-originals-{from_version}-{data['revision_id']}", originals)
        upgrade: Row = {"version": target_version, "compiled_with": version,
                        "from_version": from_version, "backup": backup.name}
        if prior_upgrade:
            upgrade["previous_upgrade"] = prior_upgrade
        data["editor_upgrade"] = upgrade
        pages = records(data, "pages")
        populate_planning_context(folder, pages)
        root = project_root.resolve() if project_root else project_for(folder)
        if root and "update_from" in data:
            link = obj(data["update_from"])
            rel = Path(text(link, "directory"))
            parent = (folder / rel).resolve()
            if (not rel.is_absolute() and parent.is_relative_to(root) and parent != folder
                    and project_for(parent) == root and (parent / FILENAME).is_file()
                    and checked_handoff(parent)["revision_id"] == link.get("revision_id")):
                changes = keyword_changes(parent, folder)
                for page in pages:
                    review = obj(page.get("revision_review", {}))
                    pid = text(page, "id")
                    if review.get("changed_in") == data["revision_id"] and pid in changes:
                        page["revision_review"] = {**review, "keyword_changes": changes[pid]}
        data["pages"] = pages
        updated[FILENAME] = encode(normalize_handoff(data))
        written: list[str] = []
        try:
            for name, content in updated.items():
                atomic_write(folder / name, content, originals[name])
                written.append(name)
            if root and root != folder:
                sync_entry_locked(root, folder, data)
        except (OSError, ValueError, KeyError):
            for name in reversed(written):
                atomic_write(folder / name, originals[name], updated[name])
            raise
        return {"status": "upgraded", "revision_id": data["revision_id"], "version": target_version,
                "backup": backup.name}
