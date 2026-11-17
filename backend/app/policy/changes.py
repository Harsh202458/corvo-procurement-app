"""Policy change operations, plain-language rendering and diffs.

An amendment (uploaded document, chat message, or hand edit) becomes a list of operations:

    {"op": "set_param", "path": "limits.spot_po_below", "value": 300000}
    {"op": "add_rule", "rule": {...}, "before": "R-5.3"}        (or "after", or neither = end)
    {"op": "update_rule", "id": "R-5.4", "changes": {"when": {...}, "then": {...}}}
    {"op": "remove_rule", "id": "R-5.4"}
    {"op": "move_rule", "id": "R-X", "before": "R-5.2"}
    {"op": "add_validation" | "update_validation" | "remove_validation", ...}
    {"op": "set_meta", "version": "5.1", "effective_from": "2026-11-19", "notes": "..."}

`apply_operations` returns a new policy dict (the input is not modified); the caller
validates it with `validate_policy` before it can be saved as a draft.
"""
from __future__ import annotations

import copy
from typing import Any

from .conditions import describe, fmt

PARAM_LABELS = {
    "clubbing.enabled": "Clubbing of similar PR lines",
    "clubbing.keys": "Lines are clubbed when these match",
    "clubbing.creation_window_days": "Clubbing window: days from the requirement's first line",
    "clubbing.need_by_window_days": "Clubbing window: need-by date within ± days",
    "clubbing.never_club_new_items": "New items are never clubbed",
    "clubbing.require_same_uom": "Only club lines with the same unit of measure",
    "valuation.price_order": "Valuation price, in order of preference",
    "valuation.last_po_lookback_days": "Valuation: last PO price lookback (days)",
    "valuation.out_of_ceiling_uses_lowest_contract_rate": "Value at the lowest contract rate when every contract is out of ceiling",
    "valuation.new_item_price": "New items are valued at",
    "contracts.selection": "Which contract is used when several apply",
    "preferred_list.export_top_n": "Suppliers per item in the preferred-list export",
    "suggestions.new_items_get_suggestions": "New items get supplier suggestions",
    "contracts.require_active_supplier": "Contracts need an Active supplier",
    "contracts.max_ceiling_utilisation": "Share of a contract ceiling that may be used",
    "history.lookback_days": "Purchase history lookback for spot/emergency POs (days)",
    "history.require_active_supplier": "Purchase history must be from an Active supplier",
    "limits.emergency_po_max": "Emergency PO limit (at or below, INR)",
    "limits.spot_po_below": "Spot PO limit (below, INR)",
    "limits.rfq_below": "RFQ limit (below, INR); at or above goes to RFP",
    "preferred_list.po_lookback_days": "Preferred list: PO lookback (days)",
    "preferred_list.include_contract_suppliers": "Preferred list includes contract suppliers",
    "preferred_list.reference_price_lookback_days": "Reference price lookback (days)",
    "preferred_list.weights": "Preferred supplier ranking weights",
    "preferred_list.recency_buckets": "Recency scoring ([days, score] buckets)",
    "suggestions.top_up_from_category": "Top up suggestions from category suppliers",
    "consolidation.enabled": "PO consolidation",
    "consolidation.keys": "Requirements share a PO when these match",
    "consolidation.window_hours": "PO consolidation window (hours)",
    "supplier_eligibility.allowed_statuses": "Supplier statuses that may receive POs or be suggested",
    "supplier_eligibility.min_on_time_pct": "Minimum supplier on-time %",
    "supplier_eligibility.min_quality_rating": "Minimum supplier quality rating",
    "supplier_eligibility.excluded_supplier_ids": "Suppliers excluded by name",
    "duplicate_window_hours": "Duplicate-line detection window (hours)",
}


class OperationError(ValueError):
    pass


def _index(items: list[dict], rid: str, kind: str) -> int:
    for i, r in enumerate(items):
        if r.get("id") == rid:
            return i
    raise OperationError(f"{kind} '{rid}' not found")


def _insert(items: list[dict], item: dict, before: str | None, after: str | None, kind: str) -> None:
    if any(r.get("id") == item.get("id") for r in items):
        raise OperationError(f"{kind} '{item.get('id')}' already exists")
    if before:
        items.insert(_index(items, before, kind), item)
    elif after:
        items.insert(_index(items, after, kind) + 1, item)
    else:
        items.append(item)


def _set_path(d: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def apply_operations(doc: dict, ops: list[dict]) -> dict:
    new = copy.deepcopy(doc)
    new.setdefault("params", {})
    new.setdefault("rules", [])
    new.setdefault("validations", [])
    for n, op in enumerate(ops, start=1):
        kind = op.get("op")
        try:
            if kind == "set_param":
                _set_path(new["params"], op["path"], op["value"])
            elif kind == "set_meta":
                for k in ("version", "effective_from", "notes", "title"):
                    if k in op:
                        new[k] = op[k]
            elif kind in ("add_rule", "add_validation"):
                key, item = ("rules", op["rule"]) if kind == "add_rule" else ("validations", op["validation"])
                _insert(new[key], copy.deepcopy(item), op.get("before"), op.get("after"), key[:-1])
            elif kind in ("update_rule", "update_validation"):
                key = "rules" if kind == "update_rule" else "validations"
                i = _index(new[key], op["id"], key[:-1])
                changes = copy.deepcopy(op.get("changes", {}))
                if "then" in changes and isinstance(changes["then"], dict):
                    changes["then"] = {**new[key][i].get("then", {}), **changes["then"]}
                new[key][i] = {**new[key][i], **changes}
            elif kind in ("remove_rule", "remove_validation"):
                key = "rules" if kind == "remove_rule" else "validations"
                new[key].pop(_index(new[key], op["id"], key[:-1]))
            elif kind == "move_rule":
                item = new["rules"].pop(_index(new["rules"], op["id"], "rule"))
                _insert(new["rules"], item, op.get("before"), op.get("after"), "rule")
            else:
                raise OperationError(f"unknown operation '{kind}'")
        except KeyError as e:
            raise OperationError(f"operation {n} ({kind}): missing field {e}") from e
        except OperationError as e:
            raise OperationError(f"operation {n} ({kind}): {e}") from e
    return new


# ---------- plain language ----------

def outcome_text(then: dict) -> str:
    route = then.get("route", "?")
    if then.get("decision") == "PO":
        who = {"contract": "the contract supplier at the contract rate",
               "category_best": "the best-rated eligible supplier of the item's category at the valuation price",
               }.get(then.get("supplier"), "the 1st preferred supplier at its reference price")
        s = f"PO ({route}) to {who}"
    elif then.get("decision") == "NOT_PROCESSED":
        s = f"not processed ({route})"
    else:
        n = then.get("suggest", 0)
        s = f"sourcing request ({route})" + (f", suggest {n} suppliers" if n else ", no supplier suggestions")
    if then.get("flags"):
        s += f" [{', '.join(then['flags'])}]"
    return s


def rule_text(rule: dict, params: dict | None = None) -> str:
    off = "" if rule.get("enabled", True) else " (disabled)"
    return f"§{rule.get('clause')} {rule.get('title')}{off}: if {describe(rule.get('when', {}), params)} → {outcome_text(rule.get('then', {}))}"


def validation_text(v: dict, params: dict | None = None) -> str:
    return f"{v.get('title')}: if {describe(v.get('when', {}), params)} → not processed ({v.get('reason')})"


def policy_summary(doc: dict) -> list[str]:
    """The whole policy as plain-language lines (for the policy page and LLM context)."""
    params = doc.get("params", {})
    out = [f"{doc.get('title')} v{doc.get('version')}, effective {doc.get('effective_from')}", "", "Parameters:"]
    for path, val in _flatten(params):
        out.append(f"- {PARAM_LABELS.get(path, path)}: {fmt(val) if val is not None else 'none'}")
    out += ["", "Line checks (applied first):"]
    out += [f"- {validation_text(v, params)}" for v in doc.get("validations", [])]
    out += ["", "Decision rules (in order, first match wins):"]
    out += [f"{i}. {rule_text(r, params)}" for i, r in enumerate(doc.get("rules", []), start=1)]
    return out


def _flatten(d: dict, prefix: str = "") -> list[tuple[str, Any]]:
    out = []
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and path not in ("preferred_list.weights",):
            out += _flatten(v, path + ".")
        else:
            out.append((path, v))
    return out


def _normalised(doc: dict) -> dict:
    """Fill defaults so a hand-written document and its validated form compare equal."""
    from .schema import PolicyInvalid, validate_policy

    try:
        return validate_policy(doc).model_dump(mode="json")
    except PolicyInvalid:
        return doc


def diff_policies(old: dict, new: dict) -> list[dict]:
    """Plain-language list of what changes from old to new. Each entry:
    {"kind": "param|rule_added|rule_removed|rule_changed|rule_order|validation_*|meta", "text": ...}"""
    old, new = _normalised(old), _normalised(new)
    changes: list[dict] = []
    for k in ("version", "effective_from"):
        if str(old.get(k)) != str(new.get(k)):
            label = "Version" if k == "version" else "Effective from"
            changes.append({"kind": "meta", "text": f"{label}: {old.get(k)} → {new.get(k)}"})

    op, np_ = dict(_flatten(old.get("params", {}))), dict(_flatten(new.get("params", {})))
    for path in list(op) + [p for p in np_ if p not in op]:
        a, b = op.get(path), np_.get(path)
        if a != b:
            label = PARAM_LABELS.get(path, path)
            if path not in op:
                changes.append({"kind": "param", "path": path, "text": f"New setting: {label} = {fmt(b)}"})
            elif path not in np_:
                changes.append({"kind": "param", "path": path, "text": f"Removed setting: {label} (was {fmt(a)})"})
            else:
                changes.append({"kind": "param", "path": path, "text": f"{label}: {fmt(a)} → {fmt(b)}"})

    for key, kind, text_fn in (("validations", "validation", validation_text), ("rules", "rule", rule_text)):
        old_items = {r["id"]: r for r in old.get(key, [])}
        new_items = {r["id"]: r for r in new.get(key, [])}
        new_order = [r["id"] for r in new.get(key, [])]
        for rid, r in new_items.items():
            if rid not in old_items:
                pos = new_order.index(rid)
                where = f"before {new_order[pos + 1]}" if pos + 1 < len(new_order) else "at the end"
                changes.append({"kind": f"{kind}_added", "id": rid,
                                "text": f"New {kind} ({where}): {text_fn(r, new.get('params'))}"})
            elif r != old_items[rid]:
                changes.append({"kind": f"{kind}_changed", "id": rid,
                                "text": f"Changed {kind} {rid}: was \"{text_fn(old_items[rid], old.get('params'))}\" "
                                        f"→ now \"{text_fn(r, new.get('params'))}\""})
        for rid, r in old_items.items():
            if rid not in new_items:
                changes.append({"kind": f"{kind}_removed", "id": rid,
                                "text": f"Removed {kind}: {text_fn(r, old.get('params'))}"})
        common_old = [i for i in (r["id"] for r in old.get(key, [])) if i in new_items]
        common_new = [i for i in new_order if i in old_items]
        if common_old != common_new:
            changes.append({"kind": f"{kind}_order", "text": f"{kind.title()} order changed: {' → '.join(common_new)}"})
    return changes
