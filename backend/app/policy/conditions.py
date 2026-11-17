"""A tiny condition language, evaluated against a dict of facts.

    {"fact": "value", "op": "lt", "value": {"param": "limits.spot_po_below"}}
    {"all": [...]}, {"any": [...]}, {"not": {...}}; {"all": []} means "always"

Values may be literals, {"param": "dotted.path"} (from the policy params) or
{"fact": "other_fact"}. String comparisons are case-insensitive.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

OPS = {"eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "between", "contains", "not_contains",
       "exists", "not_exists", "starts_with"}

OP_WORDS = {"eq": "is", "ne": "is not", "lt": "is below", "lte": "is at or below", "gt": "is above",
            "gte": "is at or above", "in": "is one of", "not_in": "is not one of", "between": "is between",
            "contains": "includes", "not_contains": "does not include", "exists": "is present",
            "not_exists": "is missing", "starts_with": "starts with"}


class ConditionError(ValueError):
    pass


def get_param(params: dict, path: str) -> Any:
    cur: Any = params
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ConditionError(f"unknown parameter '{path}'")
        cur = cur[part]
    return cur


def resolve(v: Any, facts: dict, params: dict) -> Any:
    if isinstance(v, dict) and set(v) == {"param"}:
        return get_param(params, v["param"])
    if isinstance(v, dict) and set(v) == {"fact"}:
        return facts.get(v["fact"])
    if isinstance(v, list):
        return [resolve(x, facts, params) for x in v]
    return v


def _norm(x: Any) -> Any:
    if isinstance(x, str):
        return x.strip().lower()
    if isinstance(x, datetime):
        return x.date()
    return x


def _coerce_pair(a: Any, b: Any) -> tuple[Any, Any]:
    """Compare dates with ISO strings, numbers with numeric strings."""
    if isinstance(a, (date, datetime)) and isinstance(b, str):
        try:
            b = date.fromisoformat(b[:10])
        except ValueError:
            pass
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, str):
        try:
            b = float(b.replace(",", ""))
        except ValueError:
            pass
    return _norm(a), _norm(b)


def _cmp(op: str, a: Any, b: Any) -> bool:
    if op == "exists":
        return a is not None and a != ""
    if op == "not_exists":
        return a is None or a == ""
    if op in ("in", "not_in"):
        items = b if isinstance(b, list) else [b]
        hit = a is not None and any(x == y for x, y in (_coerce_pair(a, i) for i in items))
        return hit if op == "in" else not hit
    if op in ("contains", "not_contains"):
        if isinstance(a, (list, tuple, set)):
            hit = any(_norm(x) == _norm(b) for x in a)
        elif isinstance(a, str) and isinstance(b, str):
            hit = _norm(b) in _norm(a)
        else:
            hit = False
        return hit if op == "contains" else not hit
    if a is None:
        return op == "ne" and b is not None
    if op == "between":
        lo, hi = b
        x, lo = _coerce_pair(a, lo)
        _, hi = _coerce_pair(a, hi)
        try:
            return lo <= x <= hi
        except TypeError:
            return False
    x, y = _coerce_pair(a, b)
    if op == "eq":
        return x == y
    if op == "ne":
        return x != y
    if op == "starts_with":
        return isinstance(x, str) and isinstance(y, str) and x.startswith(y)
    try:
        if op == "lt":
            return x < y
        if op == "lte":
            return x <= y
        if op == "gt":
            return x > y
        if op == "gte":
            return x >= y
    except TypeError:
        return False
    raise ConditionError(f"unknown operator '{op}'")


def evaluate(cond: dict, facts: dict, params: dict) -> bool:
    if "all" in cond:
        return all(evaluate(c, facts, params) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, facts, params) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], facts, params)
    a = facts.get(cond["fact"])
    b = resolve(cond.get("value"), facts, params)
    return _cmp(cond["op"], a, b)


# ---------- plain language ----------

def inr(x: float) -> str:
    """Indian digit grouping: 1234567 -> 12,34,567. Small non-integers keep 2 decimals."""
    neg, x = x < 0, abs(x)
    if x < 1000 and abs(x - round(x)) >= 0.005:
        return ("-" if neg else "") + f"{x:.2f}"
    s = str(int(round(x)))
    if len(s) > 3:
        head, tail, parts = s[:-3], s[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + s


def fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return inr(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(x) for x in v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return f"'{v}'" if isinstance(v, str) else str(v)


def _fmt_value(v: Any, params: dict | None) -> str:
    if isinstance(v, dict) and set(v) == {"param"}:
        if params is not None:
            try:
                return fmt(get_param(params, v["param"]))
            except ConditionError:
                pass
        return f"[{v['param']}]"
    if isinstance(v, dict) and set(v) == {"fact"}:
        return v["fact"].replace("_", " ")
    return fmt(v)


def describe(cond: dict, params: dict | None = None) -> str:
    if "all" in cond:
        parts = [describe(c, params) for c in cond["all"]]
        if not parts:
            return "always"
        return " and ".join(parts) if len(parts) > 1 else parts[0]
    if "any" in cond:
        parts = [describe(c, params) for c in cond["any"]]
        return "never" if not parts else "(" + " or ".join(parts) + ")"
    if "not" in cond:
        return f"not ({describe(cond['not'], params)})"
    fact, op, v = cond["fact"].replace("_", " "), cond["op"], cond.get("value")
    if op in ("exists", "not_exists"):
        return f"{fact} {OP_WORDS[op]}"
    if op in ("eq", "ne") and isinstance(v, bool):
        return fact if (v is True) == (op == "eq") else f"not {fact}"
    if op == "between" and isinstance(v, list) and len(v) == 2:
        return f"{fact} is between {_fmt_value(v[0], params)} and {_fmt_value(v[1], params)}"
    return f"{fact} {OP_WORDS.get(op, op)} {_fmt_value(v, params)}"


def explain(cond: dict, facts: dict, params: dict) -> list[str]:
    """Each leaf condition with the actual value and whether it held (for decision reasons)."""
    if "all" in cond or "any" in cond:
        out: list[str] = []
        for c in cond.get("all", cond.get("any", [])):
            out += explain(c, facts, params)
        return out
    if "not" in cond:
        return [f"not ({'; '.join(explain(cond['not'], facts, params))})"]
    a = facts.get(cond["fact"])
    ok = evaluate(cond, facts, params)
    return [f"{describe(cond, params)} ({cond['fact']} = {fmt(a) if a is not None else 'n/a'}: {'yes' if ok else 'no'})"]


def walk(cond: dict):
    """Yield every leaf condition."""
    if "all" in cond or "any" in cond:
        for c in cond.get("all", cond.get("any", [])):
            yield from walk(c)
    elif "not" in cond:
        yield from walk(cond["not"])
    else:
        yield cond
