"""Select comparable observations before aggregating unique queries or shared volume estimates."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from plan_records import Row, obj, require, text

BASIS_FIELDS = ("provider", "observed_at", "evidence_class", "unit", "scope", "metric_type")
METRICS = {"volume": "monthly_volume", "difficulty": "kd"}


def comparable_metrics(observations: list[dict[str, str]], keywords: dict[str, dict[str, str]],
                       policy: Row, simulation: bool) -> dict[str, Row]:
    """Use an explicit snapshot, or the sole comparable snapshot in a locale. Keep conflicting values blank."""
    selected: dict[str, tuple[str, ...]] = {}
    require(set(policy) <= METRICS.keys(), "metric_basis accepts volume and difficulty")
    for metric, spec in policy.items():
        basis = obj(spec)
        require(set(basis) <= set(BASIS_FIELDS), f"Unknown {metric} basis field")
        for required in ("provider", "observed_at", "evidence_class"):
            text(basis, required)
        selected[metric] = tuple(str(basis.get(k, "")) for k in BASIS_FIELDS)

    snapshots: dict[tuple[str, str, str], set[tuple[str, ...]]] = defaultdict(set)
    per_keyword: dict[tuple[str, str], list[tuple[tuple[str, ...], dict[str, str]]]] = defaultdict(list)
    for row in observations:
        metric, kid = row["metric"], row["keyword_id"]
        if metric not in METRICS:
            continue
        require(kid in keywords and all(row[k] == keywords[kid][k] for k in ("market", "language")),
                f"Keyword metric has an unknown ID or different locale: {kid}")
        basis_key = tuple(row[k] for k in BASIS_FIELDS)
        if row["evidence_class"] == "model_inference" or (not simulation and row["evidence_class"] == "simulation"):
            continue
        if not row["observed_at"] or row["provider"].casefold() in {"", "unknown"}:
            continue
        snapshots[row["market"], row["language"], metric].add(basis_key)
        per_keyword[kid, metric].append((basis_key, row))

    result: dict[str, Row] = {}
    for kid, word in keywords.items():
        item: Row = {}
        for metric, output in METRICS.items():
            available = snapshots[word["market"], word["language"], metric]
            basis_key = selected.get(metric, ())
            if not basis_key and len(available) == 1:
                basis_key = next(iter(available))
            rows = [r for key, r in per_keyword.get((kid, metric), []) if key == basis_key]
            numbers = {Decimal(r["value"]) for r in rows if r["value_state"] == "observed"}
            group_ids = {r.get("volume_group", "") for r in rows if r["value_state"] == "observed"}
            state = "missing"
            value = ""
            if not basis_key and len(available) > 1:
                state = "select_basis"
            elif len(numbers) > 1 or len(group_ids) > 1:
                state = "conflicting"
            elif numbers:
                number = next(iter(numbers))
                if number.is_finite() and number >= 0 and (metric != "difficulty" or number <= 100):
                    state, value = "known", format(number, "f")
                else:
                    state = "invalid"
            item[output], item[output + "_state"] = value, state
            item[output + "_refs"] = [r["source_ref"] for r in rows]
            if metric == "volume":
                item["volume_group"] = next(iter(group_ids)) if len(group_ids) == 1 else ""
            if basis_key:
                item[output + "_basis"] = dict(zip(BASIS_FIELDS, basis_key, strict=True))
        result[kid] = item
    # A known supplier pool is one estimate even when its aliases fall in different planning rows.
    pools: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for kid, item in result.items():
        if item.get("volume_group") and item["monthly_volume_state"] == "known":
            word = keywords[kid]
            pools[word["market"], word["language"], str(item["volume_group"])].append(kid)
    for members in pools.values():
        if len({Decimal(str(result[k]["monthly_volume"])) for k in members}) > 1:
            for kid in members:
                result[kid].update(monthly_volume="", monthly_volume_state="conflicting_pool")
    return result


def totals(members: Iterable[str], metrics: dict[str, Row]) -> Row:
    ids = set(members)
    known: dict[tuple[str, str], Decimal] = {}
    volume_count, kd_count = 0, 0
    bins = [0, 0, 0]
    for kid in ids:
        row = metrics[kid]
        if row["monthly_volume_state"] == "known":
            volume_count += 1
            group = str(row["volume_group"])
            known[("group", group) if group else ("keyword", kid)] = Decimal(str(row["monthly_volume"]))
        if row["kd_state"] == "known":
            kd = Decimal(str(row["kd"]))
            bins[0 if kd < 30 else 1 if kd < 60 else 2] += 1
            kd_count += 1
    return {"query_count": len(ids), "known_monthly_volume": format(sum(known.values(), Decimal(0)), "f") if known else "",
            "volume_coverage": f"{volume_count}/{len(ids)}", "kd_coverage": f"{kd_count}/{len(ids)}",
            "kd_under_30": bins[0], "kd_30_to_under_60": bins[1], "kd_60_to_100": bins[2],
            "kd_unknown": len(ids) - kd_count}
