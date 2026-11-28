"""Select comparable observations before aggregating unique queries or shared volume estimates."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from plan_records import Row, obj, require, text
from revisions import read_delivery_rows
from seo_project import local_file

BASIS_FIELDS = ("provider", "observed_at", "evidence_class", "unit", "scope", "metric_type")
METRICS = {"volume": "monthly_volume", "difficulty": "kd"}


Basis = tuple[str, ...]
Scope = tuple[str, str, str]


def policy_bases(policy: Row) -> dict[str, Basis]:
    require(set(policy) <= METRICS.keys(), "metric_basis accepts volume and difficulty")
    selected: dict[str, Basis] = {}
    for metric, spec in policy.items():
        basis = obj(spec)
        require(set(basis) <= set(BASIS_FIELDS), f"Unknown {metric} basis field")
        for field in ("provider", "observed_at", "evidence_class"):
            text(basis, field)
        selected[metric] = tuple(str(basis.get(k, "")) for k in BASIS_FIELDS)
    return selected


def previous_metric_bases(folder: Path) -> dict[Scope, Basis]:
    """Each previous query records its actual selected basis, including releases before v0.10."""
    choices: dict[Scope, Basis] = {}
    cache: dict[str, Basis] = {}
    for row in read_delivery_rows(local_file(folder, "keyword-decisions.csv")):
        for metric, column in METRICS.items():
            raw = row.get(column + "_basis", "")
            if not raw:
                continue
            if raw not in cache:
                cache[raw] = policy_bases({metric: json.loads(raw)})[metric]
            key = row["market"], row["language"], metric
            require(choices.setdefault(key, cache[raw]) == cache[raw], "Previous delivery has inconsistent metric bases")
    return choices


def metric_reading(metric: str, rows: list[dict[str, str]]) -> Row:
    observed = [r for r in rows if r["value_state"] == "observed"]
    numbers = {Decimal(r["value"]) for r in observed}
    groups = {r.get("volume_group", "") for r in observed} if metric == "volume" else set()
    output = METRICS[metric]
    state, value = "missing", ""
    if len(numbers) > 1 or len(groups) > 1:
        state = "conflicting"
    elif numbers:
        number = next(iter(numbers))
        if number.is_finite() and number >= 0 and (metric != "difficulty" or number <= 100):
            state, value = "known", format(number, "f")
        else:
            state = "invalid"
    result: Row = {output: value, output + "_state": state, output + "_refs": [r["source_ref"] for r in rows]}
    if metric == "volume":
        result["volume_group"] = next(iter(groups)) if len(groups) == 1 else ""
    return result


class MetricSnapshots:
    """Resolve each observed value once per provider snapshot and locale, before grouping for any page."""
    def __init__(self, observations: list[dict[str, str]], keywords: dict[str, dict[str, str]], policy: Row,
                 simulation: bool, locale_policies: list[Row], inherited: dict[Scope, Basis]) -> None:
        default = policy_bases(policy)
        local: dict[Scope, Basis] = {}
        for row in locale_policies:
            market, language = text(row, "market"), text(row, "language", empty=True)
            for metric, basis in policy_bases({k: v for k, v in row.items() if k not in {"market", "language"}}).items():
                key = market, language, metric
                require(key not in local, f"Duplicate metric_basis_by_locale for {key}")
                local[key] = basis
        observations_by_basis: dict[tuple[Scope, Basis], dict[str, list[dict[str, str]]]] = defaultdict(
            lambda: defaultdict(list))
        available: dict[Scope, set[Basis]] = defaultdict(set)
        for row in observations:
            kid, metric = row["keyword_id"], row["metric"]
            if metric not in METRICS:
                continue
            require(kid in keywords and all(row[k] == keywords[kid][k] for k in ("market", "language")),
                    f"Keyword metric has an unknown ID or different locale: {kid}")
            if row["evidence_class"] == "model_inference" or (not simulation and row["evidence_class"] == "simulation"):
                continue
            if not row["observed_at"] or row["provider"].casefold() in {"", "unknown"}:
                continue
            scope = row["market"], row["language"], metric
            basis = tuple(row[k] for k in BASIS_FIELDS)
            available[scope].add(basis)
            observations_by_basis[scope, basis][kid].append(row)

        self.readings: dict[tuple[Scope, Basis], dict[str, Row]] = {}
        for (scope, basis), members in observations_by_basis.items():
            metric = scope[2]
            readings = {kid: metric_reading(metric, rows) for kid, rows in members.items()}
            if metric == "volume":
                pools: dict[str, list[Row]] = defaultdict(list)
                for reading in readings.values():
                    if reading["volume_group"] and reading["monthly_volume_state"] == "known":
                        pools[str(reading["volume_group"])].append(reading)
                for aliases in pools.values():
                    if len({Decimal(str(r["monthly_volume"])) for r in aliases}) > 1:
                        for r in aliases:
                            r.update(monthly_volume="", monthly_volume_state="conflicting_pool")
            self.readings[scope, basis] = readings

        self.selected: dict[Scope, Basis] = {}
        self.metrics: dict[str, Row] = {}
        for kid, word in keywords.items():
            item: Row = {}
            for metric, output in METRICS.items():
                scope = word["market"], word["language"], metric
                choices = available[scope]
                chosen = local.get(scope) or default.get(metric) or inherited.get(scope)
                if chosen is None and len(choices) == 1:
                    chosen = next(iter(choices))
                if chosen:
                    self.selected[scope] = chosen
                reading = self.readings.get((scope, chosen or ()), {}).get(kid)
                if reading is None:
                    reading = metric_reading(metric, [])
                    if choices and chosen is None:
                        reading[output + "_state"] = "select_basis"
                    elif chosen and chosen not in choices:
                        reading[output + "_state"] = "unavailable_basis"
                item.update(reading)
                if chosen:
                    item[output + "_basis"] = dict(zip(BASIS_FIELDS, chosen, strict=True))
            self.metrics[kid] = item

    def catalogue(self, decisions: dict[str, Row]) -> list[Row]:
        members: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for kid, word in decisions.items():
            pages = members[str(word["market"]), str(word["language"])]
            pages[""].append(kid)
            if word.get("page_id"):
                pages[str(word["page_id"])].append(kid)
        output: list[Row] = []
        for scope, basis in sorted(set(self.readings) | set(self.selected.items())):
            market, language, metric = scope
            readings = self.readings.get((scope, basis), {})
            for pid, ids in members[market, language].items():
                counts = totals(ids, readings)
                fields = ("known_monthly_volume", "volume_coverage") if metric == "volume" else (
                    "kd_coverage", "kd_under_30", "kd_30_to_under_60", "kd_60_to_100", "kd_unknown")
                output.append({"market": market, "language": language, "page_id": pid, "metric": metric,
                               **dict(zip(BASIS_FIELDS, basis, strict=True)), "selected": self.selected.get(scope) == basis,
                               "available": bool(readings), "query_count": len(ids), **{f: counts[f] for f in fields}})
        return output


def comparable_metrics(observations: list[dict[str, str]], keywords: dict[str, dict[str, str]],
                       policy: Row, simulation: bool) -> dict[str, Row]:
    return MetricSnapshots(observations, keywords, policy, simulation, [], {}).metrics


def totals(members: Iterable[str], metrics: dict[str, Row]) -> Row:
    ids = set(members)
    known: dict[tuple[str, str], Decimal] = {}
    volume_count, kd_count = 0, 0
    states: dict[str, Counter[str]] = {"volume": Counter(), "kd": Counter()}
    bins = [0, 0, 0]
    for kid in ids:
        row = metrics.get(kid, {})
        states["volume"][str(row.get("monthly_volume_state", "missing"))] += 1
        states["kd"][str(row.get("kd_state", "missing"))] += 1
        if row.get("monthly_volume_state") == "known":
            volume_count += 1
            group = str(row.get("volume_group", ""))
            known[("group", group) if group else ("keyword", kid)] = Decimal(str(row["monthly_volume"]))
        if row.get("kd_state") == "known":
            kd = Decimal(str(row["kd"]))
            bins[0 if kd < 30 else 1 if kd < 60 else 2] += 1
            kd_count += 1
    def state_summary(counts: Counter[str]) -> str:
        if counts["known"]:
            return "known" if counts["known"] == len(ids) else "partial"
        return next((s for s in ("select_basis", "unavailable_basis", "conflicting_pool", "conflicting", "invalid")
                     if counts[s]), "missing")

    return {"volume_status": state_summary(states["volume"]), "kd_status": state_summary(states["kd"]),
            "query_count": len(ids), "known_monthly_volume": format(sum(known.values(), Decimal(0)), "f") if known else "",
            "volume_coverage": f"{volume_count}/{len(ids)}", "kd_coverage": f"{kd_count}/{len(ids)}",
            "kd_under_30": bins[0], "kd_30_to_under_60": bins[1], "kd_60_to_100": bins[2],
            "kd_unknown": len(ids) - kd_count}
