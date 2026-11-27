"""Join the reviewed architecture decisions to the complete query and competitor evidence."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from cluster import TOKEN
from delivery import AnalysisDelivery
from demand_metrics import comparable_metrics, totals
from keyword_evidence import PRICE_FIELDS, RANK_FIELDS, url_summary
from library import normalize, stable_id
from workbench_data import keyword_analysis, save_term_table, script_json, source_catalog
from opportunities import FIELDS as OPPORTUNITY_FIELDS, build_opportunities
from plan_records import Row, active_design_modules, index, obj, optional_records, records, require, strings, text


def workbench_template(asset: Path) -> str:
    template = asset.read_text(encoding="utf-8")
    components = {"/* __REVIEW_LAYOUT__ */": "review-layout.css", "/* __VERSION_PANEL__ */": "version-panel.js",
                  "/* __PAGE_CONFIRMATION__ */": "page-confirmation.js", "/* __OPPORTUNITIES__ */": "page-opportunities.js"}
    for marker, filename in components.items():
        if marker in template:
            template = template.replace(marker, asset.with_name(filename).read_text(encoding="utf-8"))
    return template


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def write_rows(path: Path, rows: list[Row], minimum_fields: list[str]) -> None:
    fields = list(dict.fromkeys([*minimum_fields, *(key for row in rows for key in row)]))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


def dimension_members(values: dict[str, Row], decisions: dict[str, Row]) -> dict[str, list[str]]:
    """Expand reviewed whole-token phrases in one pass. Preserve punctuation, locale and explicit exceptions."""
    assigned = {vid: dict.fromkeys(strings(row, "keyword_ids") if "keyword_ids" in row else [])
                for vid, row in values.items()}
    patterns: dict[tuple[str, str, str], list[tuple[tuple[str, ...], str]]] = defaultdict(list)
    for vid, item in values.items():
        if "aliases" not in item:
            continue
        locale = text(item, "market"), text(item, "language")
        for alias in strings(item, "aliases", required=True):
            parts = tuple(TOKEN.findall(normalize(alias)))
            require(bool(parts), f"Empty dimension alias: {vid}")
            patterns[(*locale, parts[0])].append((parts, vid))
    if patterns:
        for kid, word in decisions.items():
            tokens = tuple(TOKEN.findall(normalize(text(word, "keyword"))))
            for n, token in enumerate(tokens):
                for parts, vid in patterns.get((str(word["market"]), str(word["language"]), token), []):
                    if tokens[n:n + len(parts)] == parts:
                        assigned[vid][kid] = None
    for vid, item in values.items():
        for kid in strings(item, "exclude_keyword_ids") if "exclude_keyword_ids" in item else []:
            require(kid in decisions, f"Unknown dimension exception: {vid}/{kid}")
            assigned[vid].pop(kid, None)
    return {vid: list(ids) for vid, ids in assigned.items()}


class SearchPlan:
    """The same joins supply the page/keyword handoff, normalized CSVs and searchable offline view."""

    def __init__(self, plan: Row, pages: list[Row], decisions: list[Row],
                 keywords: dict[str, dict[str, str]], analysis: AnalysisDelivery) -> None:
        self.analysis = analysis
        self.plan = plan
        self.keywords = keywords
        self.project = obj(plan["project"])
        self.modern = plan.get("schema_version") == "0.5.0"
        self.pages = index(pages)
        self.decisions = {text(d, "keyword_id"): d for d in decisions}
        self.metrics = comparable_metrics(analysis.metrics, keywords, obj(plan.get("metric_basis", {})),
                                          self.project["mode"] == "simulation")
        self.modules: list[Row] = []
        self.values: list[Row] = []
        self.groups: list[Row] = []
        self.comparisons: list[Row] = []
        self.prices: list[Row] = [dict(r) for r in analysis.prices]
        self.urls: list[Row] = [dict(r) for r in analysis.urls]
        self.ranks: list[Row] = ([dict(r) for r in analysis.rankings] if analysis.rankings_attached else
                                 [{**r, "source_ref": r["id"], "language": keywords[str(r["keyword_id"])]["language"]}
                                  for r in records(plan, "ranking_observations")])
        if not analysis.rankings_attached:
            received = [{**{k: str(r.get(k, "")) for k in RANK_FIELDS}, "provider": str(r["source_id"]),
                         "keyword_original": keywords[str(r["keyword_id"])]["keyword_original"],
                         "estimated_traffic": str(r.get("estimated_traffic") or "") if r.get("estimated_traffic") != 0 else "0"}
                        for r in self.ranks]
            for row in received:
                if row["estimated_traffic"].lower() in {"n/a", "na", "unknown", "missing"}:
                    row["estimated_traffic"] = ""
            self.ranks = [dict(r) for r in received]
            self.urls = [dict(u) for u in url_summary(received, [])]
        self._assign(plan, analysis)
        self._group(plan)
        self.opportunities = build_opportunities(plan, self.decisions, analysis.pages, self.ranks, self.comparisons)
        buckets: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
        for kid, d in self.decisions.items():
            buckets[str(d["market"]), str(d["language"]), str(d["origin"]), str(d["coverage"])].append(kid)
        self.coverage = [{"market": market, "language": lang, "origin": origin, "coverage": cover,
                          **totals(ids, self.metrics)} for (market, lang, origin, cover), ids in buckets.items()]
        self.summary: Row = {"status": "reviewed_plan" if self.modern else "legacy_plan",
                             "keywords": len(decisions), "modules": len(self.modules), "planning_rows": len(self.groups),
                             "coverage": dict(Counter(text(d, "coverage") for d in decisions)),
                             "price_observations": len(self.prices), "ranking_rows": len(self.ranks),
                             "rank_scope": "attached_export" if analysis.rankings_attached else "plan_only"}

    def _assign(self, plan: Row, analysis: AnalysisDelivery) -> None:
        templates, groups = (index(records(plan, k)) for k in ("templates", "groups"))
        reviews = {text(r, "id"): r for r in optional_records(plan, "competitor_page_reviews") if "id" in r}
        prices = {text(r, "source_ref"): r for r in self.prices}
        modules: dict[tuple[str, str], Row] = {}
        by_page: dict[str, list[Row]] = defaultdict(list)
        ranks_by_word: dict[str, list[Row]] = defaultdict(list)
        origins: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in analysis.lineage:
            if row["keyword_id"]:
                origins[row["keyword_id"]].append(row)
        for rank in self.ranks:
            if rank.get("keyword_id") and "invalid_url" not in str(rank.get("issues", "")) and rank.get("url"):
                ranks_by_word[text(rank, "keyword_id")].append(rank)
        for pid, page in self.pages.items():
            module_ids: list[str] = []
            scope = obj(page.get("search_scope", {}))
            if self.modern:
                require(scope.get("action") in {"new", "reuse", "revise"}, f"pages[{pid}].search_scope.action")
                require(scope.get("approval") in {"proposed", "confirmed"}, f"pages[{pid}].search_scope.approval")
                text(scope, "boundary")
                text(scope, "priority_reason")
                adjacent = strings(scope, "adjacent_page_ids")
                require(set(adjacent) <= self.pages.keys() and pid not in adjacent, f"Unknown/self adjacent page: {pid}")
            peer_urls: list[str] = []
            for peer in optional_records(scope, "peer_reviews"):
                rid = text(peer, "review_id")
                require(rid in reviews, f"pages[{pid}] unknown peer review {rid}")
                review = reviews[rid]
                require(review.get("status") in {"observed", "partial", "simulated"}, f"{rid} has no page observations")
                require(review.get("status") != "simulated" or self.project["mode"] == "simulation",
                        f"{rid} simulated comparison on a real plan")
                self.comparisons.append({"page_id": pid, "review_id": rid, "url": review["url"],
                                         "page_type": review["page_type"], "status": review["status"],
                                         "observed_at": review.get("observed_at", ""), "reason": text(peer, "reason")})
                peer_urls.append(text(review, "url"))
            refs = strings(scope, "price_refs") if "price_refs" in scope else []
            for ref in refs:
                require(ref in prices and prices[ref]["market"] == self.project["market"]
                        and prices[ref]["language"] == self.project["language"], f"pages[{pid}] missing/foreign price {ref}")
                require(prices[ref]["evidence_class"] != "simulation" or self.project["mode"] == "simulation",
                        f"pages[{pid}] simulated price on a real plan")
            page.update(page_action=scope.get("action", ""), approval=scope.get("approval", "unreviewed"),
                        adjacent_page_ids=scope.get("adjacent_page_ids", []), boundary=scope.get("boundary", ""),
                        priority_reason=scope.get("priority_reason", ""), comparable_urls=unique(peer_urls),
                        price_refs=refs, market=self.project["market"], language=self.project["language"])
            design = obj(page.get("design_brief", {}))
            notes = {text(n, "module_id"): n for n in optional_records(design, "module_notes")}
            for order, module in enumerate(active_design_modules(templates[text(page, "template_id")], page), 1):
                mid = text(module, "id")
                module_ids.append(mid)
                note = notes.get(mid, {})
                if self.modern and page["page_type"] != "article":
                    require(bool(note), f"pages[{pid}] missing content brief for active module {mid}")
                    text(note, "buyer_question")
                    strings(note, "required_facts")
                result: Row = {"page_id": pid, "module_id": mid, "order": order, "name": module["name"],
                               "buyer_question": note.get("buyer_question", ""), "content": note.get("content_note", ""),
                               "required_facts": note.get("required_facts", []), "keyword_ids": []}
                modules[pid, mid] = result
                self.modules.append(result)
            page["module_ids"] = module_ids
            if self.modern:
                page["reference_urls"] = unique([*peer_urls, *strings(page, "reference_urls")])

        by_module: dict[tuple[str, str], list[str]] = defaultdict(list)
        for kid, decision in self.decisions.items():
            pid = text(decision, "page_id", empty=True)
            disposition = text(decision, "disposition")
            mid = str(decision.get("module_id", ""))
            coverage = str(decision.get("coverage", {"exclude": "out_of_scope", "defer": "pending"}.get(disposition, "unreviewed")))
            if self.modern:
                allowed = {"full", "partial"} if pid else {"pending" if disposition == "defer" else "out_of_scope"}
                require(coverage in allowed, f"decisions[{kid}]: coverage must match disposition {disposition}")
                require((pid, mid) in modules if pid else not mid, f"decisions[{kid}]: module_id must belong to its page")
                if coverage == "partial":
                    text(decision, "remaining_need")
                if pid:
                    require(decision["language"] == self.project["language"], f"decisions[{kid}] needs a known target language")
            page = self.pages.get(pid, {})
            role = "primary" if kid == page.get("primary_keyword_id") else "support" if disposition == "page" else disposition
            group = groups[text(decision, "group_id")]
            evidence = origins.get(kid, [])
            source_refs = [r["source_ref"] for r in evidence]
            origin = ("candidate" if evidence and all(r["discovery_type"] == "generated_candidate" for r in evidence)
                      else "source" if evidence else "plan_only")
            ranks = ranks_by_word.get(kid, [])
            decision.update(self.metrics[kid])
            decision.update(target_url=page.get("url", ""), role=role, module_id=mid, coverage=coverage,
                            remaining_need=decision.get("remaining_need", ""), group_label=group["label"],
                            buyer_task=group["buyer_task"], origin=origin, source_refs=source_refs,
                            source_classes=unique([r["evidence_class"] for r in evidence]),
                            ranking_refs=[text(r, "source_ref") for r in ranks],
                            ranked_urls=unique([text(r, "url") for r in ranks]), dimension_value_ids=[])
            if pid:
                by_page[pid].append(decision)
            if mid:
                by_module[pid, mid].append(kid)
        for key, module in modules.items():
            module["keyword_ids"] = by_module[key]
            module["keywords"] = [self.decisions[k]["keyword"] for k in by_module[key]]
        for pid, page in self.pages.items():
            own = by_page[pid]
            page.update(totals([text(d, "keyword_id") for d in own], self.metrics))
            page.update(support_keywords=[d["keyword"] for d in own if d["role"] == "support"],
                        section_keywords=[d["keyword"] for d in own if d["role"] in {"section", "filter"}],
                        group_ids=unique([text(d, "group_id") for d in own]),
                        ranked_urls=unique([str(url) for d in own for url in strings(d, "ranked_urls")]))

    def _group(self, plan: Row) -> None:
        values = index(records(plan, "dimension_values") if self.modern else optional_records(plan, "dimension_values"))
        expanded = dimension_members(values, self.decisions)
        memberships: dict[str, list[str]] = defaultdict(list)
        dimensions: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        labels: set[tuple[str, str, str, str]] = set()
        for vid, item in values.items():
            dimension, label = text(item, "dimension"), text(item, "value")
            ids = expanded[vid]
            require(bool(ids), f"Dimension {vid} has no members; keep unobserved combinations as candidates")
            representative = str(item.get("representative_keyword_id", ids[0]))
            require(set(ids) <= self.decisions.keys() and representative in ids, f"Invalid dimension members: {vid}")
            locales = {(text(self.decisions[k], "market"), text(self.decisions[k], "language", empty=True)) for k in ids}
            require(len(locales) == 1, f"Mixed markets or languages in dimension value: {vid}")
            market, language = next(iter(locales))
            if "aliases" in item:
                require((market, language) == (item["market"], item["language"]), f"Wrong locale for dimension {vid}")
            label_key = (dimension, label, market, language)
            require(label_key not in labels, f"Duplicate dimension value within a locale: {label_key}")
            labels.add(label_key)
            dimensions[dimension, market, language].update(ids)
            for kid in ids:
                memberships[kid].append(vid)
            self.values.append({"level": "value", "value_id": vid, "dimension": dimension, "value": label,
                                "market": market, "language": language, "definition": text(item, "definition"),
                                "aliases": item.get("aliases", []), "exclude_keyword_ids": item.get("exclude_keyword_ids", []),
                                "representative_keyword": self.decisions[representative]["keyword"],
                                "representative_keyword_id": representative, **totals(ids, self.metrics)})
        for (dimension, market, language), members in dimensions.items():
            self.values.append({"level": "dimension", "value_id": "", "dimension": dimension, "value": "",
                                "market": market, "language": language, **totals(members, self.metrics)})

        buckets: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for kid, decision in self.decisions.items():
            ids = memberships[kid]
            decision["dimension_value_ids"] = ids
            plan_ids = []
            for vid in ids or [""]:
                key = (vid, *(text(decision, k, empty=True) for k in
                              ("group_id", "page_id", "disposition", "coverage", "market", "language")))
                buckets[key].append(kid)
                plan_ids.append("pick_" + stable_id(*key))
            decision["planning_ids"] = plan_ids
        for key, members_list in buckets.items():
            vid, gid, pid, disposition, coverage, market, language = key
            value = values.get(vid, {})
            preferred = str(value.get("representative_keyword_id", ""))
            rep = preferred if preferred in members_list else members_list[0]
            words = [self.decisions[k] for k in members_list]
            page = self.pages.get(pid, {})
            self.groups.append({"planning_id": "pick_" + stable_id(*key), "value_id": vid, "dimension": value.get("dimension", ""),
                                "value": value.get("value", ""), "market": market, "language": language, "group_id": gid,
                                "buyer_task": words[0]["buyer_task"], "representative_keyword": self.decisions[rep]["keyword"],
                                "representative_keyword_id": rep, **totals(members_list, self.metrics),
                                "page_id": pid, "target_url": page.get("url", ""), "disposition": disposition, "coverage": coverage,
                                "ranked_urls": unique([u for word in words for u in strings(word, "ranked_urls")]),
                                "comparable_urls": page.get("comparable_urls", []), "business_evidence": page.get("evidence_ids", []),
                                "decision_evidence": unique([eid for w in words for eid in strings(w, "evidence_ids")]),
                                "page_rationale": page.get("rationale", ""), "reasons": unique([text(w, "reason") for w in words])})

    def write(self, stage: Path, architecture_json: str, keywords_path: Path, materials: Path | None) -> None:
        originals = source_catalog(stage, self.analysis, keywords_path, materials)
        analysis = keyword_analysis(self.plan, self.keywords, self.decisions)
        save_term_table(stage / "words-and-phrases.csv", analysis)
        write_rows(stage / "intent-groups.csv", records(analysis, "intents"), ["id", "label", "buyer_task"])
        write_rows(stage / "input-files.csv", [{k: v for k, v in row.items() if k not in {"preview", "columns"}}
                                              for row in originals], ["id", "name", "source_id", "download", "sha256"])
        tables = [
            ("page-opportunities", self.opportunities, OPPORTUNITY_FIELDS),
            ("demand-coverage", self.coverage, ["market", "language", "origin", "coverage", "query_count"]),
            ("commercial-planning", self.groups, ["planning_id", "value_id", "page_id", "group_id"]),
            ("dimension-values", self.values, ["level", "value_id", "dimension", "value", "market", "language", "query_count"]),
            ("content-modules", self.modules, ["page_id", "module_id", "order", "name", "buyer_question", "content",
                                               "required_facts", "keyword_ids"]),
            ("comparable-pages", self.comparisons, ["page_id", "review_id", "url", "page_type", "status", "observed_at", "reason"]),
            ("price-observations", self.prices, PRICE_FIELDS),
            ("received-rankings", self.ranks, RANK_FIELDS),
        ]
        for filename, rows, fields in tables:
            write_rows(stage / f"{filename}.csv", rows, fields)
        page_fields = ("id", "name", "name_en", "name_zh", "url", "page_type", "parent_id", "template_id", "priority",
                       "objective", "rationale", "evidence_ids", "fact_gaps", "primary_keyword", "boundary", "approval",
                       "page_action", "state", "priority_reason", "adjacent_page_ids", "price_refs", "query_count",
                       "volume_coverage", "known_monthly_volume", "kd_coverage")
        # Normalize the high-volume HTML tables. CSVs retain the complete analytical fields.
        bases: list[object] = []
        basis_ids: dict[str, int] = {}
        word_fields = ("keyword_id", "keyword", "market", "language", "role", "coverage", "origin", "group_id",
                       "buyer_task", "target_url", "page_id", "disposition", "module_id", "reason", "remaining_need",
                       "dimension_value_ids", "monthly_volume", "kd", "monthly_volume_state", "kd_state",
                       "monthly_volume_refs", "kd_refs", "source_refs", "source_classes", "ranking_refs", "planning_ids",
                       "volume_group")
        packed_words: list[list[object]] = []
        for decision in self.decisions.values():
            cells = [decision.get(k, "") for k in word_fields]
            for field in ("monthly_volume_basis", "kd_basis"):
                basis = decision.get(field)
                if basis is None:
                    cells.append(None)
                    continue
                key = json.dumps(basis, sort_keys=True)
                if key not in basis_ids:
                    basis_ids[key] = len(bases)
                    bases.append(basis)
                cells.append(basis_ids[key])
            packed_words.append(cells)
        rank_fields = ("source_ref", "keyword_id", "keyword_original", "provider", "competitor_id", "market", "language",
                       "observed_at", "url", "evidence_class", "position")
        (stage / "workbench-context.js").write_text("window.architectureContext = null;\n", encoding="utf-8")
        payload = {"project": self.project, "summary": self.summary, "metric_bases": bases,
                   "opportunities": self.opportunities,
                   "opportunity_offers": {text(o, "id"): text(o, "label") for o in records(self.plan, "offerings")},
                   **analysis, "inputs": originals, "page_observations": self.analysis.pages,
                   "word_fields": [*word_fields, "volume_basis_id", "kd_basis_id"], "rank_fields": rank_fields,
                   "pages": [{k: p.get(k, "") for k in page_fields} for p in self.pages.values()],
                   "word_rows": packed_words, "modules": [{k: v for k, v in m.items() if k != "keywords"} for m in self.modules],
                   "groups": self.groups,
                   "values": self.values, "coverage": self.coverage, "prices": self.prices, "comparisons": self.comparisons,
                   "urls": self.urls, "rank_rows": [[r.get(k, "") for k in rank_fields] for r in self.ranks]}
        asset = Path(__file__).resolve().parents[1] / "assets/search-plan.html"
        template = workbench_template(asset)
        require(template.count("__SEARCH_PLAN__") == 1, "Search plan template placeholder missing or repeated")
        require(template.count("__ARCHITECTURE_DATA__") == 1, "Workbench architecture data placeholder missing")
        rendered = template.replace("__ARCHITECTURE_DATA__", architecture_json, 1)
        # Replace the original marker only, even if an input string contains the marker text.
        before, after = rendered.rsplit('id="search-data">__SEARCH_PLAN__</script>', 1)
        (stage / "index.html").write_text(before + 'id="search-data">' + script_json(payload) + '</script>' + after,
                                         encoding="utf-8")
