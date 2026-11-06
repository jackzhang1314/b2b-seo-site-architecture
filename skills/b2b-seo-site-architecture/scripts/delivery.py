"""Attach the explicitly selected analysis runs to one portable planning delivery."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from keyword_evidence import url_summary
from library import obj, read_table, write_json, write_table


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("Analysis delivery: " + message)


def rows(path: Path) -> list[dict[str, str]]:
    return [row for _, row in read_table(path)]


@dataclass
class AnalysisDelivery:
    files: list[tuple[Path, str, str]] = field(default_factory=list)
    runs: list[dict[str, object]] = field(default_factory=list)
    urls: list[dict[str, str]] = field(default_factory=list)
    pages: list[dict[str, str]] = field(default_factory=list)
    rankings_attached: bool = False

    def summary(self) -> dict[str, object]:
        return {"status": "attached" if self.runs else "not_attached", "runs": self.runs,
                "files": len(self.files), "rankings_attached": self.rankings_attached,
                "url_snapshots": len(self.urls), "page_metric_rows": len(self.pages),
                "scope": "Selected analysis runs only; page and keyword traffic are separate, never added."}

    def write(self, stage: Path) -> None:
        for source, relative, sha in self.files:
            check(digest(source) == sha, f"file changed during delivery: {relative}; rerun with stable inputs")
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        if self.rankings_attached:
            for name, data in (("competitor-url-summary.csv", self.urls), ("page-traffic-observations.csv", self.pages)):
                # Even an empty table keeps a usable, explicit header.
                write_table(stage / name, data, list(data[0]) if data else ["url", "page_estimated_traffic"])
        write_json(stage / "analysis-manifest.json", {**self.summary(),
            "artifacts": [{"path": relative, "sha256": sha} for _, relative, sha in self.files]})
        lines = ["# 本轮完整分析文件", "", "逐词流量与整页流量分列，不能相加；空值不是零。", ""]
        if not self.runs:
            lines += ["未附带分析目录。本交付仅含计划内记录；不能据此声称已交付完整原表分析。",
                      "生成时用 --analysis-dir 指向本轮选定工具运行目录，见 Skill 输出规范。"]
        else:
            lines += [f"已附带 {len(self.runs)} 个运行目录、{len(self.files)} 个原始分析文件。", ""]
            if self.rankings_attached:
                lines += ["- [完整URL汇总](competitor-url-summary.csv)：含只有整页流量、没有逐词明细的URL。",
                          "- [整页流量原记录](page-traffic-observations.csv)",
                          "- [规划内Top30与实访关联](ranked-url-summary.csv)：口径独立，不冒充整页流量。", ""]
            lines += [f"- [{relative}]({relative})" for _, relative, _ in self.files]
        (stage / "analysis-index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_analysis(directory: Path | None, keywords_path: Path, competitor_ids: set[str]) -> AnalysisDelivery:
    delivery = AnalysisDelivery()
    if directory is None:
        return delivery
    check(directory.is_dir(), f"directory does not exist: {directory}")
    manifests = ([directory / "run.json"] if (directory / "run.json").is_file()
                 else sorted(directory.glob("*/run.json")))
    check(bool(manifests), "no run.json found; select a tool output or a directory of this run's outputs")
    expected_sha = digest(keywords_path)
    singletons: set[str] = set()
    for manifest in manifests:
        run = obj(json.loads(manifest.read_text(encoding="utf-8-sig")))
        command = str(run.get("command", ""))
        check(command in {"build", "review", "frequency", "semantic-prepare", "semantic-check", "provider", "rankings", "drilldown"},
              f"unrecognized run command in {manifest}: {command!r}")
        folder = manifest.parent
        if command in {"build", "rankings"}:
            check(command not in singletons, f"multiple {command} runs; select one current run rather than a history directory")
            singletons.add(command)
        if command in {"build", "review"}:
            check((folder / "keywords.csv").is_file() and digest(folder / "keywords.csv") == expected_sha,
                  f"{folder.name}/keywords.csv differs from --keywords; select the matching current analysis")
        else:
            sha = run.get("keywords_sha256", run.get("input_sha256"))
            check(sha == expected_sha, f"keyword hash mismatch in {folder.name}; analysis must use the same complete keyword input")
        if command == "rankings":
            ranks = rows(folder / "ranking-observations.csv")
            delivery.pages = rows(folder / "page-traffic-observations.csv")
            check({r["competitor_id"] for r in ranks + delivery.pages} <= competitor_ids,
                  "ranking/page traffic competitor_id is absent from plan.competitors")
            delivery.urls = url_summary(ranks, delivery.pages)
            check(delivery.urls == rows(folder / "competitor-url-summary.csv"),
                  "URL summary differs from observations; rerun keyword_evidence.py rankings with the current Skill")
            delivery.rankings_attached = True
        run_files = sorted(p for p in folder.rglob("*") if p.is_file())
        for path in run_files:
            check(not path.is_symlink() and path.resolve().is_relative_to(folder.resolve()),
                  f"analysis artifact must be a regular file within the selected run: {path.name}")
            check(path.suffix in {".csv", ".xlsx", ".json", ".jsonl", ".md"},
                  f"unexpected artifact {path.name}; select only analysis outputs")
            relative = (Path("analysis") / folder.name / path.relative_to(folder)).as_posix()
            delivery.files.append((path, relative, digest(path)))
        delivery.runs.append({"directory": "analysis/" + folder.name, "command": command,
                              "version": run.get("version", "unknown"), "files": len(run_files)})
    return delivery
