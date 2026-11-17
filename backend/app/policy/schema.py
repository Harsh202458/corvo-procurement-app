"""The policy document format, and validation (structure + meaning).

A policy is one JSON document: metadata, parameters (limits, windows, weights), line
validation rules, and ordered decision rules (first match wins). Parameter groups allow
extra keys so an amendment can introduce a new named limit and reference it from a rule.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .conditions import OPS, ConditionError, get_param, walk
from .facts import is_known_fact

DECISIONS = ("PO", "SOURCING_REQUEST", "NOT_PROCESSED")
SUPPLIER_STRATEGIES = ("contract", "preferred", "category_best", "none")
PRICE_SOURCES = ("contract_rate", "last_po_price", "standard_price", "estimated_unit_price")
CLUB_KEYS = ("item_code", "plant", "uom", "department", "requester", "urgency")
CONSOLIDATION_KEYS = ("supplier_id", "plant", "route", "decision", "category")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Clubbing(_Open):
    enabled: bool = True
    keys: list[str] = Field(default_factory=lambda: ["item_code", "plant"])
    creation_window_days: float = 14
    need_by_window_days: float = 7
    never_club_new_items: bool = True
    require_same_uom: bool = True


class Valuation(_Open):
    price_order: list[str] = Field(default_factory=lambda: ["contract_rate", "last_po_price", "standard_price"])
    last_po_lookback_days: int = 365
    out_of_ceiling_uses_lowest_contract_rate: bool = True
    new_item_price: str = "estimated_unit_price"


class Contracts(_Open):
    require_active_supplier: bool = True
    max_ceiling_utilisation: float = 1.0  # 1.0 = may use the full ceiling
    selection: Literal["lowest_rate_with_ceiling"] = "lowest_rate_with_ceiling"


class History(_Open):
    lookback_days: int = 365
    require_active_supplier: bool = True


class PreferredList(_Open):
    po_lookback_days: int = 548
    include_contract_suppliers: bool = True
    reference_price_lookback_days: int = 548
    recency_buckets: list[list[float]] = Field(default_factory=lambda: [[90, 1.0], [365, 0.5]])
    weights: dict[str, float] = Field(default_factory=lambda: {
        "frequency": 0.30, "recency": 0.25, "contract": 0.20, "on_time": 0.15, "quality": 0.10})
    export_top_n: int = 3


class Suggestions(_Open):
    top_up_from_category: bool = True
    new_items_get_suggestions: bool = False


class Consolidation(_Open):
    enabled: bool = True
    keys: list[str] = Field(default_factory=lambda: ["supplier_id", "plant", "route"])
    window_hours: float = 24


class SupplierEligibility(_Open):
    allowed_statuses: list[str] = Field(default_factory=lambda: ["Active"])
    min_on_time_pct: float | None = None
    min_quality_rating: float | None = None
    excluded_supplier_ids: list[str] = Field(default_factory=list)


class Params(_Open):
    clubbing: Clubbing = Field(default_factory=Clubbing)
    valuation: Valuation = Field(default_factory=Valuation)
    contracts: Contracts = Field(default_factory=Contracts)
    history: History = Field(default_factory=History)
    limits: dict[str, float] = Field(default_factory=dict)
    preferred_list: PreferredList = Field(default_factory=PreferredList)
    suggestions: Suggestions = Field(default_factory=Suggestions)
    consolidation: Consolidation = Field(default_factory=Consolidation)
    supplier_eligibility: SupplierEligibility = Field(default_factory=SupplierEligibility)
    duplicate_window_hours: float = 24


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["PO", "SOURCING_REQUEST", "NOT_PROCESSED"]
    route: str
    supplier: Literal["contract", "preferred", "category_best", "none"] = "none"
    suggest: int = 0
    flags: list[str] = Field(default_factory=list)  # e.g. capex_approval
    note: str | None = None

    @field_validator("route")
    @classmethod
    def _route(cls, v: str) -> str:
        v = v.strip().upper().replace(" ", "_")
        if not v:
            raise ValueError("route must not be empty")
        return v


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    clause: str  # policy clause cited in decisions, e.g. "5.4"
    title: str
    when: dict[str, Any]
    then: Outcome
    enabled: bool = True
    description: str | None = None


class ValidationRule(BaseModel):
    """Line-level: if `when` holds, the line is NOT_PROCESSED with `reason`."""
    model_config = ConfigDict(extra="forbid")
    id: str
    clause: str
    title: str
    when: dict[str, Any]
    reason: str
    enabled: bool = True


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str = "CORVO-PROC-POL-04"
    version: str
    effective_from: date
    title: str = "Corvo Procurement Policy"
    notes: str | None = None
    params: Params = Field(default_factory=Params)
    validations: list[ValidationRule] = Field(default_factory=list)
    rules: list[Rule]


class PolicyInvalid(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _check_condition(cond: Any, level: str, params: dict, where: str, errors: list[str]) -> None:
    if not isinstance(cond, dict):
        errors.append(f"{where}: condition must be an object")
        return
    keys = set(cond)
    if keys & {"all", "any"}:
        k = "all" if "all" in cond else "any"
        if len(keys) != 1 or not isinstance(cond[k], list):
            errors.append(f"{where}: '{k}' must be the only key and hold a list")
            return
        for i, c in enumerate(cond[k]):
            _check_condition(c, level, params, f"{where}.{k}[{i}]", errors)
        return
    if "not" in keys:
        if len(keys) != 1:
            errors.append(f"{where}: 'not' must be the only key")
            return
        _check_condition(cond["not"], level, params, f"{where}.not", errors)
        return
    if not {"fact", "op"} <= keys or keys - {"fact", "op", "value"}:
        errors.append(f"{where}: a leaf condition needs 'fact' and 'op' (and usually 'value'), got {sorted(keys)}")
        return
    if not is_known_fact(cond["fact"], level):
        errors.append(f"{where}: unknown {level} fact '{cond['fact']}'")
    if cond["op"] not in OPS:
        errors.append(f"{where}: unknown operator '{cond['op']}'")
    if cond["op"] not in ("exists", "not_exists") and "value" not in cond:
        errors.append(f"{where}: operator '{cond['op']}' needs a value")
    vals = cond.get("value")
    for v in (vals if isinstance(vals, list) else [vals]):
        if isinstance(v, dict):
            if set(v) == {"param"}:
                try:
                    get_param(params, v["param"])
                except ConditionError as e:
                    errors.append(f"{where}: {e}")
            elif set(v) == {"fact"}:
                if not is_known_fact(v["fact"], level):
                    errors.append(f"{where}: unknown {level} fact '{v['fact']}' in value")
            else:
                errors.append(f"{where}: value object must be {{'param': ...}} or {{'fact': ...}}")
    if cond["op"] == "between" and not (isinstance(vals, list) and len(vals) == 2):
        errors.append(f"{where}: 'between' needs a [low, high] list")


def validate_policy(doc: dict | PolicyDocument) -> PolicyDocument:
    """Parse and check a policy. Raises PolicyInvalid with every problem found."""
    try:
        p = doc if isinstance(doc, PolicyDocument) else PolicyDocument.model_validate(doc)
    except ValidationError as e:
        raise PolicyInvalid([f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()])
    errors: list[str] = []
    params = p.params.model_dump()
    ids = [r.id for r in p.rules] + [v.id for v in p.validations]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errors.append(f"duplicate rule ids: {sorted(dupes)}")
    if not any(r.enabled for r in p.rules):
        errors.append("policy has no enabled decision rules")
    for r in p.rules:
        _check_condition(r.when, "requirement", params, f"rule {r.id}", errors)
        if r.then.decision == "PO" and r.then.supplier == "none":
            errors.append(f"rule {r.id}: a PO outcome needs supplier 'contract', 'preferred' or 'category_best'")
        if r.then.decision == "SOURCING_REQUEST" and r.then.supplier != "none":
            errors.append(f"rule {r.id}: a sourcing request does not pick a single supplier (use suggest)")
        if r.then.supplier == "contract":
            leaves = list(walk(r.when))
            if not any(c.get("fact") == "contract_available" for c in leaves):
                errors.append(f"rule {r.id}: uses the contract supplier, so it must test contract_available")
        if not 0 <= r.then.suggest <= 20:
            errors.append(f"rule {r.id}: suggest must be between 0 and 20")
    for v in p.validations:
        _check_condition(v.when, "line", params, f"validation {v.id}", errors)
    pv = p.params
    for s in pv.valuation.price_order:
        if s not in PRICE_SOURCES:
            errors.append(f"params.valuation.price_order: unknown price source '{s}'")
    for k in pv.clubbing.keys:
        if k not in CLUB_KEYS:
            errors.append(f"params.clubbing.keys: unsupported key '{k}' (allowed: {', '.join(CLUB_KEYS)})")
    for k in pv.consolidation.keys:
        if k not in CONSOLIDATION_KEYS:
            errors.append(f"params.consolidation.keys: unsupported key '{k}' (allowed: {', '.join(CONSOLIDATION_KEYS)})")
    for name, w in pv.preferred_list.weights.items():
        if w < 0:
            errors.append(f"params.preferred_list.weights.{name}: must not be negative")
    for name, val in pv.limits.items():
        if val < 0:
            errors.append(f"params.limits.{name}: must not be negative")
    if pv.clubbing.creation_window_days < 0 or pv.clubbing.need_by_window_days < 0:
        errors.append("params.clubbing: windows must not be negative")
    if not 0 < pv.contracts.max_ceiling_utilisation <= 1.5:
        errors.append("params.contracts.max_ceiling_utilisation must be in (0, 1.5]")
    if errors:
        raise PolicyInvalid(errors)
    return p
