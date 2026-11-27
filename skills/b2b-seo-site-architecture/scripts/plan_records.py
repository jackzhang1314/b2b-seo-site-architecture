"""Shared field readers for the versioned architecture plan."""
from __future__ import annotations

Row = dict[str, object]


def obj(value: object) -> Row:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("Expected an object with string keys")
    return {str(k): v for k, v in value.items()}


def text(row: Row, key: str, *, empty: bool = False) -> str:
    value = row.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"Expected {'possibly empty ' if empty else ''}string: {key}")
    return value


def strings(row: Row, key: str, *, required: bool = False, unique: bool = True) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"Expected string list: {key}")
    result = [str(v) for v in value]
    if required and not result:
        raise ValueError(f"Expected nonempty list: {key}")
    if unique and len(result) != len(set(result)):
        raise ValueError(f"Duplicate values in {key}")
    return result


def records(row: Row, key: str) -> list[Row]:
    value = row.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Expected object list: {key}")
    return [obj(v) for v in value]


def optional_records(row: Row, key: str) -> list[Row]:
    return records(row, key) if key in row else []


def index(rows: list[Row]) -> dict[str, Row]:
    result: dict[str, Row] = {}
    for row in rows:
        ident = text(row, "id")
        if ident in result:
            raise ValueError(f"Duplicate id: {ident}")
        result[ident] = row
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def active_design_modules(template: Row, page: Row) -> list[Row]:
    if "design_spec" not in template:
        return []
    return [m for m in records(obj(template["design_spec"]), "modules")
            if not strings(m, "variants") or page["variant"] in strings(m, "variants")]
