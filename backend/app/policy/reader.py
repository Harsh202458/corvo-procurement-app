"""Read a policy amendment (document text or a chat message) into change operations.

Two readers, same output:
  * LLM reader: the model gets the current policy, the fact vocabulary and the operation
    format, and returns operations. They are applied and validated here; on errors the
    model is asked to repair them (up to 2 times).
  * Rule-based reader: regexes for the common changes (limits, windows, dates, excluded
    suppliers). Used when no LLM is configured or the LLM fails.
Either way nothing is saved or activated here: the caller shows the computed diff and a
person approves it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from ..ingest import parse_date
from ..llm import LLMClient, LLMUnavailable
from .changes import PARAM_LABELS, OperationError, apply_operations, diff_policies
from .facts import vocabulary_text
from .schema import PolicyInvalid, validate_policy


@dataclass
class Proposal:
    method: str  # llm | rules
    operations: list[dict]
    summary: str = ""
    assumptions: list[str] = field(default_factory=list)
    unclear: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    document: dict | None = None
    changes: list[dict] = field(default_factory=list)
    llm_note: str | None = None

    @property
    def valid(self) -> bool:
        return not self.errors and self.document is not None

    def as_dict(self) -> dict:
        return {"method": self.method, "operations": self.operations, "summary": self.summary,
                "assumptions": self.assumptions, "unclear": self.unclear, "errors": self.errors,
                "valid": self.valid, "document": self.document, "changes": self.changes, "llm_note": self.llm_note}


# ------------------------------------------------------------------ helpers

def next_version(v: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)$", str(v).strip())
    return f"{m.group(1)}.{int(m.group(2)) + 1}" if m else f"{v}-amended"


def _finalise(base: dict, ops: list[dict], today: date) -> tuple[list[dict], dict, list[str], list[str]]:
    """Make sure version and effective date are set; apply and validate."""
    notes: list[str] = []
    meta = next((o for o in ops if o.get("op") == "set_meta"), None)
    if meta is None:
        meta = {"op": "set_meta"}
        ops = [meta] + ops
    if not meta.get("version") or str(meta.get("version")) == str(base.get("version")):
        meta["version"] = next_version(base.get("version", "5.0"))
        notes.append(f"No new version number given: using {meta['version']}.")
    eff = parse_date(str(meta.get("effective_from") or ""))
    if eff is None:
        meta["effective_from"] = today.isoformat()
        notes.append(f"No effective date found: defaulted to today ({today}). Check it before approving; "
                     "requirements dated before it stay on the previous version.")
    else:
        meta["effective_from"] = eff.isoformat()
    errors: list[str] = []
    doc = None
    try:
        new = apply_operations(base, ops)
        doc = validate_policy(new).model_dump(mode="json")
    except OperationError as e:
        errors = [str(e)]
    except PolicyInvalid as e:
        errors = e.errors
    return ops, doc, errors, notes


# ------------------------------------------------------------------ LLM reader

OPERATIONS_SPEC = """
Operations (apply in order):
- {"op":"set_meta","version":"5.1","effective_from":"YYYY-MM-DD","notes":"..."}
- {"op":"set_param","path":"limits.spot_po_below","value":300000}    (any dotted path under params; creates new keys)
- {"op":"add_rule","rule":RULE,"before":"R-5.4"}   (or "after":"R-5.2"; neither = append at end)
- {"op":"update_rule","id":"R-5.3","changes":{"when":COND,"then":{...},"title":"...","enabled":false}}  ("then" is merged)
- {"op":"remove_rule","id":"R-5.5"}
- {"op":"move_rule","id":"R-A1","before":"R-5.2"}
- {"op":"add_validation","validation":VALIDATION,"before":"V2"} / update_validation / remove_validation (same shapes)

RULE = {"id":"R-A1","clause":"5.4A","title":"short title","description":"one sentence",
        "when":COND,"then":{"decision":"PO"|"SOURCING_REQUEST"|"NOT_PROCESSED","route":"SPOT_PO",
        "supplier":"contract"|"preferred"|"category_best"|"none","suggest":0,"flags":[]}}
  - decision PO needs supplier "contract" (the rule must test contract_available: contract supplier at contract
    rate), "preferred" (1st preferred supplier at its reference price; needs purchase history) or
    "category_best" (best-rated eligible supplier listing the item's category, at the valuation price, e.g. the
    item master standard price for never-bought items). SOURCING_REQUEST uses supplier "none" and suggest N.
VALIDATION = {"id":"V6","clause":"A2","title":"...","when":COND(line-level facts),"reason":"why the line is not processed"}
COND = {"fact":"value","op":"lt","value":200000} | {"all":[COND,...]} | {"any":[COND,...]} | {"not":COND}
  ops: eq ne lt lte gt gte in not_in between contains not_contains exists not_exists starts_with
  value may be a literal, {"param":"limits.name"} or {"fact":"other_fact"}. Strings compare case-insensitively.
"""

GUIDELINES = """
How to translate:
1. Change only what the amendment says. Do not restate unchanged rules.
2. A new number for an existing limit/window/weight -> set_param on that path (keep rules referencing params).
3. A new threshold -> set_param "limits.<snake_case_name>" and reference it with {"param": ...} in the rule.
4. Rules are evaluated top to bottom and the FIRST rule that fits decides. An exception or a narrower
   rule must be placed BEFORE the general rule it overrides (use "before"). Think about what currently
   happens to the affected requirements and make sure your change produces the amended outcome, and that
   other requirements are unaffected.
4b. Placement default: the top rules are R-1.2 (new items), R-5.1 (capex) and R-5.2 (rate contracts).
   Put a new rule AFTER R-5.2, directly before the rule it restricts or replaces (usually R-5.3 emergency
   or R-5.4 spot), unless the amendment explicitly says it applies even to new items, capex items or
   items with a usable rate contract. Say where you placed it and why in "assumptions".
5. Plant-, category-, department- or item-type-specific changes: add conditions on plant (PUN1/CHK1),
   category (exact names below), item_type (Material/Service/Capex), departments (list, use contains).
   A limit that changes for one plant only: keep the general rule and add/adjust a rule for that plant.
6. Keep existing route names (CONTRACT_PO, EMERGENCY_PO, SPOT_PO, RFQ, RFP, CAPEX_SOURCING,
   NEW_ITEM_SOURCING). Use a new route name only if the amendment defines one.
7. Blocking or excluding suppliers -> set_param supplier_eligibility.excluded_supplier_ids (full list);
   minimum ratings -> supplier_eligibility.min_on_time_pct / min_quality_rating.
8. New rule ids: "R-A1", "R-A2", ...; validation ids "V6", "V7", ...; clause = the amendment's clause
   number if it has one, else "A1", "A2", ...
9. Effective date and version: exactly as stated (YYYY-MM-DD). If not stated, leave them null.
10. Never invent requirements. Put anything ambiguous or that you could not express in "unclear", and any
   interpretation you made in "assumptions".
Return JSON only: {"summary": "plain-language summary of the amendment", "version": str|null,
"effective_from": "YYYY-MM-DD"|null, "operations": [...], "assumptions": [...], "unclear": [...]}
"""


def _context(base: dict, categories: list[str], plants: list[str]) -> str:
    rules = [{k: r[k] for k in ("id", "clause", "title", "when", "then") if k in r} for r in base.get("rules", [])]
    vals = [{k: v[k] for k in ("id", "clause", "title", "when", "reason")} for v in base.get("validations", [])]
    return "\n".join([
        f"CURRENT POLICY: version {base.get('version')}, effective {base.get('effective_from')}",
        "PARAMETERS (current values):", json.dumps(base.get("params", {}), ensure_ascii=False),
        "PARAMETER MEANINGS:", json.dumps(PARAM_LABELS, ensure_ascii=False),
        "LINE CHECKS (applied before rules):", json.dumps(vals, ensure_ascii=False),
        "DECISION RULES (in order):", json.dumps(rules, ensure_ascii=False),
        "", vocabulary_text(),
        "", f"Item categories: {', '.join(categories)}", f"Plants: {', '.join(plants)}",
    ])


def read_with_llm(text: str, base: dict, llm: LLMClient, *, today: date, categories: list[str],
                  plants: list[str], max_repairs: int = 2) -> Proposal:
    system = ("You translate procurement policy amendments into JSON edit operations for a rules engine.\n"
              + OPERATIONS_SPEC + GUIDELINES)
    user = _context(base, categories, plants) + "\n\nAMENDMENT TEXT:\n" + text[:30000]
    attempt_errors: list[str] = []
    out: dict = {}
    for attempt in range(max_repairs + 1):
        prompt = user if not attempt_errors else (
            user + "\n\nYOUR PREVIOUS ANSWER:\n" + json.dumps(out, ensure_ascii=False)
            + "\n\nIT FAILED VALIDATION WITH THESE ERRORS:\n- " + "\n- ".join(attempt_errors)
            + "\nReturn the corrected complete JSON.")
        out = llm.complete_json(system, prompt)
        if not isinstance(out, dict):
            attempt_errors = ["answer must be a JSON object"]
            continue
        ops = [o for o in out.get("operations") or [] if isinstance(o, dict)]
        meta = next((o for o in ops if o.get("op") == "set_meta"), None)
        if meta is None and (out.get("version") or out.get("effective_from")):
            ops.insert(0, {"op": "set_meta"})
            meta = ops[0]
        if meta is not None:
            if not meta.get("version"):
                meta["version"] = out.get("version")
            if not meta.get("effective_from"):
                meta["effective_from"] = out.get("effective_from")
        ops, doc, errors, notes = _finalise(base, ops, today)
        p = Proposal("llm", ops, out.get("summary", ""), list(out.get("assumptions") or []) + notes,
                     list(out.get("unclear") or []), errors, doc)
        if not errors:
            p.changes = diff_policies(base, doc)
            if attempt:
                p.llm_note = f"repaired after {attempt} validation round(s)"
            return p
        attempt_errors = errors
    p.llm_note = "the AI reading still failed validation; fix the operations by hand"
    return p


# ------------------------------------------------------------------ rule-based reader

_AMOUNT = r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|lac|crore|cr|k)?"


def parse_amount(num: str, unit: str | None) -> float:
    v = float(num.replace(",", ""))
    unit = (unit or "").lower()
    if unit in ("lakh", "lakhs", "lac"):
        v *= 100000
    elif unit in ("crore", "cr"):
        v *= 10000000
    elif unit == "k":
        v *= 1000
    return v


LIMIT_WORDS = [
    (r"emergency", "limits.emergency_po_max"),
    (r"spot", "limits.spot_po_below"),
    (r"\brfp\b|\brfq\b|request for (?:quotation|proposal)", "limits.rfq_below"),
]


def read_with_rules(text: str, base: dict, *, today: date) -> Proposal:
    ops: list[dict] = []
    unclear: list[str] = []
    assumptions: list[str] = []
    flat = re.sub(r"\s+", " ", text)
    meta: dict = {"op": "set_meta"}
    if m := re.search(r"version\s*(?:no\.?|number)?\s*[:\-]?\s*v?(\d+\.\d+)", flat, re.I):
        meta["version"] = m.group(1)
    if m := re.search(r"effective(?:\s+(?:from|on|date|as of))?\s*[:\-]?\s*"
                      r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})", flat, re.I):
        meta["effective_from"] = (parse_date(m.group(1)) or today).isoformat()
    ops.append(meta)
    handled = 0
    for sent in re.split(r"(?<=[.;])\s+|\n", text):
        s = sent.strip()
        low = s.lower()
        if not s:
            continue
        hit = False
        amounts = [parse_amount(n, u) for n, u in re.findall(_AMOUNT, low) if re.search(r"\d", n)
                   and parse_amount(n, u) >= 1000]
        if amounts and re.search(r"limit|threshold|below|up to|upto|ceiling|value", low):
            for pat, path in LIMIT_WORDS:
                if re.search(pat, low):
                    ops.append({"op": "set_param", "path": path, "value": amounts[-1]})
                    hit = True
                    break
        if m := re.search(r"(\d+)\s*(?:calendar\s+)?days?", low):
            if "need-by" in low or "need by" in low:
                ops.append({"op": "set_param", "path": "clubbing.need_by_window_days", "value": int(m.group(1))})
                hit = True
            elif "club" in low or "split" in low:
                ops.append({"op": "set_param", "path": "clubbing.creation_window_days", "value": int(m.group(1))})
                hit = True
        if (m := re.search(r"(\d+)\s*hours?", low)) and ("consolidat" in low or "combin" in low or "same po" in low):
            ops.append({"op": "set_param", "path": "consolidation.window_hours", "value": int(m.group(1))})
            hit = True
        sups = re.findall(r"\bS\d{3}\b", s)
        if sups and re.search(r"block|exclude|debar|blacklist|not be (?:used|suggested)", low):
            cur = set(base.get("params", {}).get("supplier_eligibility", {}).get("excluded_supplier_ids", []))
            ops.append({"op": "set_param", "path": "supplier_eligibility.excluded_supplier_ids",
                        "value": sorted(cur | set(sups))})
            hit = True
        if hit:
            handled += 1
        elif re.search(r"\b(shall|must|will|should|only|always|never|required)\b", low) and len(s) > 25:
            unclear.append(s)
    if handled == 0:
        unclear.insert(0, "The rule-based reader found no limit, window, date or supplier change it understands.")
    if unclear:
        assumptions.append("Sentences listed under 'unclear' need to be added by hand in the editor.")
    ops, doc, errors, notes = _finalise(base, ops, today)
    p = Proposal("rules", ops, f"Rule-based reading: {handled} change(s) recognised.", assumptions + notes,
                 unclear, errors, doc)
    if doc:
        p.changes = diff_policies(base, doc)
    return p


def read_amendment(text: str, base: dict, llm: LLMClient, *, today: date, categories: list[str],
                   plants: list[str]) -> Proposal:
    try:
        return read_with_llm(text, base, llm, today=today, categories=categories, plants=plants)
    except LLMUnavailable as e:
        p = read_with_rules(text, base, today=today)
        p.llm_note = f"AI reader unavailable ({str(e)[:160]}); used the rule-based reader"
        return p
