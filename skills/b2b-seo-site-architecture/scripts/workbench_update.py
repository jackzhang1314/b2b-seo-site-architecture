"""Carry a reviewed page's decisions and receipts into an explicitly related new planning revision."""
from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from page_confirmation import FILENAME, encode, normalize_handoff, read_handoff
from plan_records import Row, index, obj, records, require, text
from register_project import atomic_write, project_lock
from revisions import normalized_url as normalized_url, page_signatures, read_delivery_rows, read_revisions
from seo_project import INDEX, local_file, project_for
from update_inputs import preserve_received

CHOICES_AND_RECEIPTS = ("decision", "decision_version", "decided_at", "seo_decision", "seo_version",
                        "seo_decided_at", "choice_history", "history")


def project_scope(project: Row) -> tuple[str, str, str, str]:
    return (normalized_url(text(project, "site_url")), text(project, "market").casefold(),
            text(project, "language").casefold(), text(project, "mode"))


@contextmanager
def workbench_lock(folder: Path, project_root: Path | None = None) -> Iterator[None]:
    folder = folder.resolve()
    root = (project_root or project_for(folder) or folder).resolve()
    with ExitStack() as locks:
        locks.enter_context(project_lock(root))
        if root != folder:
            locks.enter_context(project_lock(folder))
        yield


def page_key(page: Row) -> tuple[str, str]:
    return normalized_url(text(page, "url")), text(page, "page_type")


def ref(page: Row) -> Row:
    return {key: page[key] for key in ("id", "url", "page_type")}


def checked_handoff(folder: Path, raw: bytes | None = None) -> Row:
    require((folder / FILENAME).is_file(), "The previous workbench needs a 0.8 or newer saved page handoff")
    data = read_handoff(folder) if raw is None else normalize_handoff(obj(json.loads(raw)))
    project = obj(obj(json.loads(local_file(folder, "architecture-plan.json").read_bytes()))["project"])
    require(project_scope(project) == project_scope(obj(data["project"])), "Previous plan and handoff name different sites or markets")
    table = index([{k: row[k] for k in ("id", "url", "page_type")}
                   for row in read_delivery_rows(local_file(folder, "pages.csv"))])
    saved = index(records(data, "pages"))
    require(table.keys() == saved.keys() and all(page_key(p) == page_key(table[k]) for k, p in saved.items()),
            "Previous handoff page ID, full URL or page type differs from the saved planning table")
    return data


def state_digest(data: Row) -> str:
    pages = index(records(data, "pages"))
    fields = ("id", "url", "page_type", *CHOICES_AND_RECEIPTS, "revision_review")
    payload = [data["revision_id"], data["change_number"], project_scope(obj(data["project"])),
               [{k: pages[pid].get(k) for k in fields} for pid in sorted(pages)]]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def planning_digest(signatures: dict[str, tuple[str, str]]) -> str:
    return hashlib.sha256(json.dumps(signatures, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


@dataclass
class UpdateBase:
    folder: Path
    root: Path | None
    data: Row
    digest: str
    signatures: dict[str, tuple[str, str]]

    @classmethod
    def read(cls, previous: Path, out: Path, root: Path | None, project: Row) -> UpdateBase:
        folder = previous.resolve()
        require(folder.is_dir() and not out.resolve().is_relative_to(folder),
                "Place the new revision in a separate output directory outside the previous workbench")
        if root:
            require(folder != root.resolve() and folder.is_relative_to(root.resolve()),
                    "Place both versions within the same customer website project")
            enclosing = project_for(folder)
            require(enclosing is None or enclosing == root.resolve(), "The previous workbench belongs to a nested project")
        with workbench_lock(folder, root):
            data = checked_handoff(folder)
            require(project_scope(obj(data["project"])) == project_scope(project),
                    "Previous and new plans must match the full site URL, market, language and evidence mode")
            return cls(folder, root, data, state_digest(data), page_signatures(folder))

    def apply(self, stage: Path, out: Path) -> None:
        data_changes = preserve_received(self.folder, stage)
        path = local_file(stage, FILENAME)
        original = path.read_bytes()
        new = normalize_handoff(obj(json.loads(original)))
        require(new["revision_id"] != self.data["revision_id"], "New revision requires its own unique ID")
        old = index(records(self.data, "pages"))
        current = index(records(new, "pages"))
        prior_urls: dict[str, list[str]] = defaultdict(list)
        current_urls: dict[str, list[str]] = defaultdict(list)
        for target, collection in ((prior_urls, old), (current_urls, current)):
            for pid, page in collection.items():
                target[page_key(page)[0]].append(pid)
        signatures = page_signatures(stage)
        for pid, page in current.items():
            url, kind = page_key(page)
            prior = old.get(pid)
            candidates = list(dict.fromkeys(([pid] if prior is not None else []) + prior_urls[url]))
            evidence = [ref(old[k]) for k in candidates]
            review: Row = {"source_pages": evidence, "source_revision_id": self.data["revision_id"]}
            if len(current_urls[url]) > 1 or len(candidates) > 1:
                status = "conflict"
                review["reason"] = "页面编号与规范网址指向多个候选，请核对页面归属。"
            elif prior is None:
                status = "conflict" if candidates else "new"
                review["reason"] = "旧版该网址使用其他页面编号。" if candidates else "旧版未规划这个页面。"
            elif page_key(prior)[0] != url:
                status = "moved"
                review["reason"] = "页面编号相同，两版规划的完整网址不同。"
            elif page_key(prior)[1] != kind:
                status = "conflict"
                review["reason"] = "页面编号和网址相同，页型已改变。"
            else:
                for key in CHOICES_AND_RECEIPTS:
                    page[key] = prior[key]
                status = "unchanged" if signatures[pid][1] == self.signatures[pid][1] else "changed"
                prior_review = obj(prior.get("revision_review", {}))
                if status == "changed":
                    review["changed_in"] = new["revision_id"]
                    labels = {"objective": "页面职责", "boundary": "相邻页边界", "module_ids": "内容模块",
                              "primary_keyword": "主词", "comparable_urls": "同类页依据", "template_id": "模板",
                              "parent_id": "层级", "fact_gaps": "企业资料缺口", "priority": "优先级"}
                    changed = [label for key, label in labels.items() if prior.get(key) != page.get(key)]
                    review["reason"] = ("本版已调整" + "、".join(changed) + "。" if changed else
                                        "本版调整了逐词归属、采购问题、页面简报或同类页依据。")
                elif prior_review.get("status") == "changed":
                    # Earlier verified work remains factual. Further unchanged revisions keep its review context.
                    review["changed_in"] = prior_review.get("changed_in", self.data["revision_id"])
                    review["reason"] = prior_review.get("reason", "上版的内容调整已延续到本版。")
                    status = "changed"
            review["status"] = status
            page["revision_review"] = review
        new["pages"] = list(current.values())
        new["update_from"] = {"revision_id": self.data["revision_id"],
                              "directory": Path(os.path.relpath(self.folder, out.resolve())).as_posix(),
                              "change_number": self.data["change_number"], "state_sha256": self.digest,
                              "planning_sha256": planning_digest(self.signatures), "data_changes": data_changes,
                              "removed_pages": [ref(p) for k, p in old.items() if k not in current]}
        atomic_write(path, encode(normalize_handoff(new)), original)

    def publish(self, stage: Path, out: Path) -> None:
        with workbench_lock(self.folder, self.root):
            require(state_digest(checked_handoff(self.folder)) == self.digest and page_signatures(self.folder) == self.signatures,
                    "The previous workbench changed during compilation. Retry --update-from with its latest saved state.")
            require(not out.exists(), "Output appeared during execution; choose another directory")
            stage.rename(out)


def verify_update_base(root: Path, folder: Path, source_locks: ExitStack | None = None) -> None:
    """Called while registration holds the project lock, immediately before adopting a derived revision."""
    if not (folder / FILENAME).is_file():
        return
    folder, root = folder.resolve(), root.resolve()
    data = read_handoff(folder)
    if "update_from" not in data:
        return
    agents = root / "AGENTS.md"
    entries = read_revisions(root, agents.read_text(encoding="utf-8-sig")) if agents.is_file() else {}
    visited = {folder}
    while "update_from" in data:
        source = obj(data["update_from"])
        relative = Path(text(source, "directory"))
        previous = (folder / relative).resolve()
        require(not relative.is_absolute() and previous.is_relative_to(root) and previous not in visited,
                "The previous version must be available inside this website project without a cycle")
        require((previous / FILENAME).is_file(), "Restore the linked previous workbench before adopting this update")
        if source_locks and previous != root:
            # A 0.8 editor takes only the folder lock. Hold it through the caller's adoption transaction.
            source_locks.enter_context(project_lock(previous))
        latest = checked_handoff(previous)
        require(latest["revision_id"] == source["revision_id"] and state_digest(latest) == source["state_sha256"]
                and planning_digest(page_signatures(previous)) == source["planning_sha256"],
                f"The previous workbench {previous.name} has newer choices or receipts. Keep both versions, "
                "compare their page records, and compile a new directory using --update-from with the latest baseline. "
                "Reconcile any independent draft edits there before adopting it.")
        entry = entries.get(previous / INDEX)
        if entry and entry[0] in {"adopted", "previous"}:
            return
        visited.add(previous)
        folder, data = previous, latest
