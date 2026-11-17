"""The decision engine: PR lines + master data + approved policy versions -> decisions.

Pure and deterministic. Steps, in policy order:
  1. match items (1.1, 1.2) and compute line facts
  2. line checks (policy `validations`) -> NOT_PROCESSED
  3. club lines into requirements (2.2), date them (2.3)
  4. per requirement, in requirement-date order (ceiling consumption order, 4.2):
     contracts (4.1-4.4), valuation (3.1), history, preferred list (6), then the ordered
     decision rules (5.x), supplier/price or suggestions (6.4), contract ceiling consumption
  5. consolidate POs (7.1, 7.2)
Every number, window and rule comes from the policy version in force on the requirement date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..policy.conditions import evaluate, explain, fmt, inr
from ..policy.schema import Params, Rule
from ..policy.versions import ApprovedVersion, select_version
from .matching import ItemMatcher, MatchResult
from .preferred import RankedSupplier, SupplierIndex, supplier_eligible
from .types import Contract, MasterData, PRLine


# ---------------------------------------------------------------- results

@dataclass
class LineResult:
    line: PRLine
    match: MatchResult
    resolved_item_code: str  # item code, or NEW
    facts: dict
    version: ApprovedVersion
    not_processed: str | None = None  # reason
    not_processed_clause: str | None = None
    requirement: "Requirement | None" = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Requirement:
    seq: int
    group_id: str
    lines: list[LineResult]
    item_code: str  # or NEW
    plant: str
    uom: str
    first_created: datetime
    anchor_need_by: date | None
    version: ApprovedVersion
    facts: dict = field(default_factory=dict)
    rule: Rule | None = None
    decision: str = ""
    route: str = ""
    clause: str = ""
    supplier_id: str | None = None
    unit_price: float | None = None
    value: float = 0.0
    suggestions: list[str] = field(default_factory=list)
    suggestion_notes: list[str] = field(default_factory=list)
    preferred: list[RankedSupplier] = field(default_factory=list)
    po_id: str | None = None
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    @property
    def requirement_date(self) -> date:
        return self.first_created.date()


@dataclass
class EngineResult:
    lines: list[LineResult]
    requirements: list[Requirement]
    purchase_orders: dict[str, list[Requirement]]
    contract_consumption: dict[str, float]

    def rows(self) -> list[dict]:
        """One row per PR line, in input order, in the brief's output format (section 4)."""
        out = []
        for lr in self.lines:
            ln, r = lr.line, lr.requirement
            if r is None:
                out.append({
                    "pr_no": ln.pr_no, "pr_line": ln.pr_line, "resolved_item_code": lr.resolved_item_code,
                    "group_id": "", "po_id": "", "decision": "NOT_PROCESSED", "route": "NOT_PROCESSED",
                    "supplier_id": "", "unit_price": "", "group_value_inr": "", "suggested_supplier_ids": "",
                    "policy_version": lr.version.label,
                    "reason": f"v{lr.version.label} §{lr.not_processed_clause or 'data'}: {lr.not_processed}",
                })
                continue
            out.append({
                "pr_no": ln.pr_no, "pr_line": ln.pr_line, "resolved_item_code": lr.resolved_item_code,
                "group_id": r.group_id, "po_id": r.po_id or "", "decision": r.decision, "route": r.route,
                "supplier_id": r.supplier_id or "",
                "unit_price": "" if r.unit_price is None else round(r.unit_price, 2),
                "group_value_inr": round(r.value, 2),
                "suggested_supplier_ids": ";".join(r.suggestions),
                "policy_version": r.version.label, "reason": r.reason,
            })
        return out


OUTPUT_COLUMNS = ["pr_no", "pr_line", "resolved_item_code", "group_id", "po_id", "decision", "route",
                  "supplier_id", "unit_price", "group_value_inr", "suggested_supplier_ids", "policy_version", "reason"]


# ---------------------------------------------------------------- engine

class Engine:
    def __init__(self, master: MasterData, versions: list[ApprovedVersion], matcher: ItemMatcher | None = None):
        if not versions:
            raise ValueError("no approved policy version")
        self.master = master
        self.versions = versions
        self.matcher = matcher or ItemMatcher(master.items)
        self.index = SupplierIndex(master)

    def version_on(self, d: date) -> ApprovedVersion:
        return select_version(self.versions, d)

    # ------------------------------------------------ step 1-2: lines
    def _line_facts(self, ln: PRLine, m: MatchResult) -> dict:
        item = self.master.items.get(m.item_code) if m.item_code else None
        return {
            "pr_no": ln.pr_no, "plant": ln.plant, "plant_known": ln.plant in self.master.plants,
            "quantity": ln.quantity, "uom": ln.uom,
            "uom_matches_item": item is None or not ln.uom or item.uom.upper() == ln.uom.upper(),
            "has_need_by": ln.need_by_date is not None,
            "need_by_before_created": bool(ln.need_by_date and ln.need_by_date < ln.created_at.date()),
            "has_item_reference": bool(ln.item_code or ln.description.strip()),
            "item_code_given": bool(ln.item_code),
            "item_code_known": (not ln.item_code) or ln.item_code in self.master.items,
            "is_new_item": item is None,
            "item_type": item.item_type if item else "", "category": item.category if item else "",
            "estimated_unit_price": ln.estimated_unit_price, "urgency": ln.urgency,
            "department": ln.department, "requester": ln.requester, "is_duplicate": False,
            **{f"pr.{k}": v for k, v in ln.extra.items()},
        }

    def _prepare_lines(self, lines: list[PRLine]) -> list[LineResult]:
        out: list[LineResult] = []
        for ln in sorted(lines, key=lambda x: x.sort_key):
            m = self.matcher.match(ln.item_code, ln.description, ln.uom)
            v = self.version_on(ln.created_at.date())
            lr = LineResult(ln, m, m.item_code or "NEW", self._line_facts(ln, m), v)
            out.append(lr)
        # line checks, in creation order; a duplicate is judged only against lines still being processed (D12)
        for i, lr in enumerate(out):
            win = timedelta(hours=lr.version.document.params.duplicate_window_hours)
            c = lr.line
            for prev in out[:i]:
                p = prev.line
                if prev.not_processed or lr.resolved_item_code == "NEW":
                    continue
                if (p.requester, p.plant, prev.resolved_item_code, p.quantity, p.need_by_date) ==                         (c.requester, c.plant, lr.resolved_item_code, c.quantity, c.need_by_date)                         and c.created_at - p.created_at <= win:
                    lr.facts["is_duplicate"] = True
                    lr.facts["duplicate_of"] = f"{p.pr_no}/{p.pr_line}"
                    lr.notes.append(f"same requester, item, quantity and need-by as {p.pr_no}/{p.pr_line}")
                    break
            params = lr.version.document.params.model_dump()
            for vr in lr.version.document.validations:
                if vr.enabled and evaluate(vr.when, lr.facts, params):
                    lr.not_processed, lr.not_processed_clause = vr.reason, vr.clause
                    if lr.facts.get("duplicate_of") and vr.when.get("fact") == "is_duplicate":
                        lr.not_processed += f" (duplicate of {lr.facts['duplicate_of']})"
                    break
        return out

    # ------------------------------------------------ step 3: clubbing
    def _club(self, lines: list[LineResult]) -> list[Requirement]:
        reqs: list[Requirement] = []
        for lr in lines:
            if lr.not_processed:
                continue
            ln = lr.line
            joined = None
            if lr.resolved_item_code != "NEW":
                for r in reqs:  # earliest first
                    cp = r.version.document.params.clubbing
                    if not cp.enabled or r.item_code == "NEW":
                        continue
                    if not self._same_keys(r, lr, cp.keys) or (cp.require_same_uom and r.uom != ln.uom):
                        continue
                    if (ln.created_at.date() - r.requirement_date).days > cp.creation_window_days:
                        continue
                    if r.anchor_need_by is None or ln.need_by_date is None or \
                            abs((ln.need_by_date - r.anchor_need_by).days) > cp.need_by_window_days:
                        continue
                    joined = r
                    break
            elif not lr.version.document.params.clubbing.never_club_new_items:
                lr.notes.append("new items are clubbed only by exact description")  # policy option off by default
            if joined:
                joined.lines.append(lr)
                lr.requirement = joined
            else:
                r = Requirement(seq=len(reqs) + 1, group_id=f"REQ-{len(reqs) + 1:04d}", lines=[lr],
                                item_code=lr.resolved_item_code, plant=ln.plant, uom=ln.uom,
                                first_created=ln.created_at, anchor_need_by=ln.need_by_date,
                                version=self.version_on(ln.created_at.date()))
                reqs.append(r)
                lr.requirement = r
        return reqs

    @staticmethod
    def _same_keys(r: Requirement, lr: LineResult, keys: list[str]) -> bool:
        first = r.lines[0]
        for k in keys:
            a = first.resolved_item_code if k == "item_code" else getattr(first.line, k, None)
            b = lr.resolved_item_code if k == "item_code" else getattr(lr.line, k, None)
            if (a or "").strip().lower() != (b or "").strip().lower():
                return False
        return True

    # ------------------------------------------------ step 4: decide
    def _contracts(self, r: Requirement, qty: float, params: Params, ledger: dict[str, float]):
        """4.1 applicable contracts; 4.2 ceiling; 4.3 lowest rate with room."""
        on = r.requirement_date
        applicable: list[tuple[Contract, float]] = []
        rejected: list[str] = []
        for c in self.index.contracts_by_item.get(r.item_code, []):
            rate = c.rate_for(r.item_code)
            if rate is None:
                continue
            if not (c.start_date <= on <= c.end_date):
                rejected.append(f"{c.contract_id} not valid on {on} ({c.start_date} to {c.end_date})")
                continue
            if params.contracts.require_active_supplier:
                ok, why = supplier_eligible(self.master.suppliers.get(c.supplier_id), params)
                if not ok:
                    rejected.append(f"{c.contract_id} supplier {c.supplier_id} {why}")
                    continue
            applicable.append((c, rate))
        usable = []
        for c, rate in applicable:
            cap = c.value_cap * params.contracts.max_ceiling_utilisation
            left = cap - ledger.get(c.contract_id, c.consumed_value)
            if qty * rate <= left + 1e-6:
                usable.append((c, rate, left))
            else:
                rejected.append(f"{c.contract_id} ceiling left {inr(max(left, 0))} < {inr(qty * rate)} needed")
        usable.sort(key=lambda x: (x[1], x[0].contract_id))
        return applicable, usable, rejected

    def _decide(self, r: Requirement, ledger: dict[str, float]) -> None:
        doc = r.version.document
        params = doc.params
        pdict = params.model_dump()
        on = r.requirement_date
        item = self.master.items.get(r.item_code)
        qty = sum(lr.line.quantity or 0 for lr in r.lines)
        ev: dict = {"lines": [f"{lr.line.pr_no}/{lr.line.pr_line}" for lr in r.lines], "notes": []}

        # history (rules 5.3/5.4) and last PO price (3.1b)
        hist_from = on - timedelta(days=params.history.lookback_days)
        hist = self.index.pos_between(r.item_code, hist_from, on) if item else []
        elig_hist = [p for p in hist if not params.history.require_active_supplier
                     or supplier_eligible(self.master.suppliers.get(p.supplier_id), params)[0]]
        val_from = on - timedelta(days=params.valuation.last_po_lookback_days)
        val_pos = self.index.pos_between(r.item_code, val_from, on) if item else []
        last_po = val_pos[-1] if val_pos else None
        all_pos = self.index.pos_by_item.get(r.item_code, []) if item else []
        prior = [p for p in all_pos if p.po_date <= on]

        applicable, usable, contract_notes = self._contracts(r, qty, params, ledger) if item else ([], [], [])
        best = usable[0] if usable else None

        # valuation 3.1
        unit_price, price_source = None, None
        if item is None:
            unit_price, price_source = r.lines[0].line.estimated_unit_price, "estimated_unit_price"
            if unit_price is None:
                ev["notes"].append("new item without an estimated unit price: valued at 0")
                unit_price = 0.0
        else:
            for src in params.valuation.price_order:
                if src == "contract_rate":
                    if best:
                        unit_price, price_source = best[1], "contract_rate"
                    elif applicable and params.valuation.out_of_ceiling_uses_lowest_contract_rate:
                        c, rate = min(applicable, key=lambda x: (x[1], x[0].contract_id))
                        unit_price, price_source = rate, "contract_rate"
                        ev["notes"].append(f"every applicable contract is out of ceiling; valued at lowest contract rate ({c.contract_id})")
                elif src == "last_po_price" and last_po:
                    unit_price, price_source = last_po.unit_price, "last_po_price"
                elif src == "standard_price":
                    unit_price, price_source = item.standard_price, "standard_price"
                elif src == "estimated_unit_price" and r.lines[0].line.estimated_unit_price is not None:
                    unit_price, price_source = r.lines[0].line.estimated_unit_price, "estimated_unit_price"
                if unit_price is not None:
                    break
            if unit_price is None:
                unit_price, price_source = item.standard_price, "standard_price"
        value = qty * unit_price
        r.value = value
        if price_source == "last_po_price" and last_po:
            ev["price"] = f"last PO {last_po.po_number} ({last_po.po_date}, {last_po.supplier_id}) at {fmt(last_po.unit_price)}"
        elif price_source == "contract_rate":
            ev["price"] = f"contract rate {fmt(unit_price)}"
        else:
            ev["price"] = f"{price_source.replace('_', ' ')} {fmt(unit_price)}"

        preferred, excluded = self.index.preferred_list(r.item_code, on, params) if item else ([], [])
        r.preferred = preferred
        cat_sups = self.index.category_suppliers(item.category, params, set()) if item else []

        need_bys = [lr.line.need_by_date for lr in r.lines if lr.line.need_by_date]
        emergency = any(lr.line.urgency.lower() == "emergency" for lr in r.lines)
        first_extra = {k: v for k, v in r.lines[0].facts.items() if k.startswith("pr.")}
        f = {
            "item_code": r.item_code, "item_description": item.description if item else r.lines[0].line.description,
            "item_type": item.item_type if item else "", "category": item.category if item else "",
            "uom": r.uom, "plant": r.plant, "is_new_item": item is None,
            "value": round(value, 2), "unit_price": unit_price, "price_source": price_source,
            "standard_price": item.standard_price if item else None, "total_quantity": qty,
            "line_count": len(r.lines), "requirement_date": on,
            "earliest_need_by": min(need_bys) if need_bys else None,
            "days_to_need_by": (min(need_bys) - on).days if need_bys else None,
            "is_emergency": emergency, "urgency": "Emergency" if emergency else "Normal",
            "departments": sorted({lr.line.department for lr in r.lines if lr.line.department}),
            "requesters": sorted({lr.line.requester for lr in r.lines if lr.line.requester}),
            "contract_available": best is not None, "contract_applies": bool(applicable),
            "contract_id": best[0].contract_id if best else None, "contract_rate": best[1] if best else None,
            "contract_supplier_id": best[0].supplier_id if best else None,
            "contract_ceiling_left": round(best[2], 2) if best else None,
            "bought_recently": bool(elig_hist),
            "days_since_last_purchase": (on - prior[-1].po_date).days if prior else None,
            "last_po_price": last_po.unit_price if last_po else None, "po_count_lookback": len(hist),
            "preferred_count": len(preferred),
            "preferred_supplier_id": preferred[0].supplier_id if preferred else None,
            "preferred_supplier_on_time_pct": preferred[0].on_time_pct if preferred else None,
            "preferred_supplier_quality": preferred[0].quality_rating if preferred else None,
            "preferred_reference_price": preferred[0].reference_price if preferred else None,
            "category_supplier_count": len(cat_sups),
            **first_extra,
        }
        r.facts = f
        if contract_notes:
            ev["contracts_not_used"] = contract_notes
        if excluded:
            ev["excluded_suppliers"] = [f"{e.supplier_id} ({e.reason})" for e in excluded]
        if elig_hist:
            p = elig_hist[-1]
            ev["history"] = f"last eligible purchase PO {p.po_number} on {p.po_date} from {p.supplier_id}"
        for lr in r.lines:
            ev["notes"] += [f"{lr.line.pr_no}/{lr.line.pr_line}: {n}" for n in lr.notes]
            if lr.match.method == "description":
                ev["notes"].append(f"{lr.line.pr_no}/{lr.line.pr_line}: {lr.match.note}")
            elif lr.match.method == "none":
                ev["notes"].append(f"{lr.line.pr_no}/{lr.line.pr_line}: {lr.match.note}")

        # ordered rules: first match wins (5.x); a PO rule with no usable supplier falls through
        skipped = []
        for rule in doc.rules:
            if not rule.enabled or not evaluate(rule.when, f, pdict):
                continue
            then = rule.then
            if then.decision == "PO" and then.supplier == "preferred":
                choice = next((s for s in preferred if s.reference_price is not None), None)
                if choice is None:
                    skipped.append(f"§{rule.clause} fits but no eligible preferred supplier with a reference price")
                    continue
                r.supplier_id, r.unit_price = choice.supplier_id, choice.reference_price
                ev["supplier"] = f"1st preferred {choice.why()}; reference price from {choice.reference_source}"
            elif then.decision == "PO" and then.supplier == "contract":
                if best is None:
                    skipped.append(f"§{rule.clause} fits but no contract has ceiling left")
                    continue
                c, rate, left = best
                r.supplier_id, r.unit_price = c.supplier_id, rate
                ledger[c.contract_id] = ledger.get(c.contract_id, c.consumed_value) + qty * rate
                ev["contract"] = (f"{c.contract_id} ({c.supplier_id}, {c.source}) rate {fmt(rate)}, ceiling left "
                                  f"{inr(left)} before, {inr(left - qty * rate)} after")
            r.rule, r.decision, r.route, r.clause = rule, then.decision, then.route, rule.clause
            r.flags = list(then.flags)
            if then.decision == "SOURCING_REQUEST":
                self._suggest(r, then.suggest, preferred, cat_sups, params)
            r.evidence = {**ev, "skipped": skipped, "conditions": explain(rule.when, f, pdict)}
            break
        else:
            r.decision, r.route, r.clause = "NOT_PROCESSED", "NOT_PROCESSED", "5"
            r.evidence = {**ev, "skipped": skipped}
        r.reason = self._reason(r)

    def _suggest(self, r: Requirement, n: int, preferred: list[RankedSupplier], cat_sups, params: Params) -> None:
        if r.item_code == "NEW" and not params.suggestions.new_items_get_suggestions:
            return
        picks = [s.supplier_id for s in preferred][:n]
        notes = [f"{s.supplier_id}: preferred #{i + 1} ({s.why()})" for i, s in enumerate(preferred[:n])]
        if len(picks) < n and params.suggestions.top_up_from_category:
            for s in cat_sups:
                if len(picks) >= n:
                    break
                if s.supplier_id not in picks:
                    picks.append(s.supplier_id)
                    notes.append(f"{s.supplier_id}: category supplier (on-time {s.on_time_pct:g}%, quality {s.quality_rating:g})")
        if len(picks) < n:
            notes.append(f"only {len(picks)} eligible supplier(s) found for {n} needed")
        r.suggestions, r.suggestion_notes = picks, notes

    def _reason(self, r: Requirement) -> str:
        v = r.version.label
        parts = []
        if r.rule is None:
            parts.append(f"v{v} §5: no decision rule matched")
        else:
            parts.append(f"v{v} §{r.clause} {r.rule.title}")
            parts.append("because " + "; ".join(r.evidence.get("conditions", [])))
        parts.append(f"value {inr(r.value)} = {fmt(r.facts.get('total_quantity'))} x {r.evidence.get('price')}")
        if r.evidence.get("contract"):
            parts.append("contract " + r.evidence["contract"])
        if r.evidence.get("supplier"):
            parts.append(r.evidence["supplier"])
        if r.suggestions:
            parts.append("suggested " + ", ".join(r.suggestions))
        if r.evidence.get("history") and r.route in ("SPOT_PO", "EMERGENCY_PO"):
            parts.append(r.evidence["history"])
        for k in ("contracts_not_used", "skipped", "excluded_suppliers"):
            if r.evidence.get(k):
                parts.append(f"{k.replace('_', ' ')}: " + "; ".join(r.evidence[k]))
        if r.flags:
            parts.append("flags: " + ", ".join(r.flags))
        if len(r.lines) > 1:
            parts.append(f"clubbed {len(r.lines)} lines (§2.2): " + ", ".join(r.evidence["lines"]))
        if r.evidence.get("notes"):
            parts.append("notes: " + "; ".join(r.evidence["notes"]))
        return " | ".join(parts)

    # ------------------------------------------------ step 5: consolidation
    def _consolidate(self, reqs: list[Requirement]) -> dict[str, list[Requirement]]:
        pos: dict[str, list[Requirement]] = {}
        open_pos: list[tuple[str, Requirement]] = []  # (po_id, first requirement)
        for r in sorted((r for r in reqs if r.decision == "PO"), key=lambda r: (r.first_created, r.seq)):
            joined = None
            for po_id, first in open_pos:
                cp = first.version.document.params.consolidation
                if not cp.enabled:
                    continue
                if any(self._cons_key(first, k) != self._cons_key(r, k) for k in cp.keys):
                    continue
                if r.first_created - first.first_created > timedelta(hours=cp.window_hours):
                    continue
                joined = po_id
                break
            if joined is None:
                joined = f"PO-{len(open_pos) + 1:04d}"
                open_pos.append((joined, r))
                pos[joined] = []
            pos[joined].append(r)
            r.po_id = joined
            if len(pos[joined]) > 1:
                r.reason += f" | consolidated into {joined} with {pos[joined][0].group_id} (§7.1)"
        return pos

    @staticmethod
    def _cons_key(r: Requirement, k: str):
        return {"supplier_id": r.supplier_id, "plant": r.plant, "route": r.route, "decision": r.decision,
                "category": r.facts.get("category")}.get(k)

    # ------------------------------------------------ run
    def run(self, lines: list[PRLine]) -> EngineResult:
        prepared = self._prepare_lines(lines)
        reqs = self._club(prepared)
        ledger: dict[str, float] = {}
        for r in sorted(reqs, key=lambda r: (r.first_created, r.seq)):  # 4.2: ceiling in requirement-date order
            self._decide(r, ledger)
        pos = self._consolidate(reqs)
        order = {ln.key: i for i, ln in enumerate(lines)}
        prepared.sort(key=lambda lr: order.get(lr.line.key, 0))
        return EngineResult(prepared, reqs, pos, ledger)
