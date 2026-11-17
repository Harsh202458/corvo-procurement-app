"""Preferred supplier list (policy 6) and supplier eligibility (5.7).

Ranking (6.2) is a weighted score of five signals, each scaled to 0..1:
    frequency  POs for the item in the lookback / the most POs any listed supplier has
    recency    bucket of days since the supplier's last PO for the item (default <=90d: 1, <=365d: 0.5, else 0)
    contract   1 if the supplier has held a contract for the item (started on or before the date)
    on_time    supplier master on-time % / 100
    quality    supplier master quality rating / 5
Weights and buckets live in the policy (params.preferred_list), so they are versioned and
can be amended. Ties: later last PO, then higher on-time %, then supplier id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..policy.schema import Params
from .types import Contract, MasterData, PurchaseRecord, Supplier


def supplier_eligible(s: Supplier | None, params: Params) -> tuple[bool, str]:
    """Policy 5.7 plus any eligibility settings an amendment adds."""
    if s is None:
        return False, "not in supplier master"
    el = params.supplier_eligibility
    if s.status.strip().lower() not in {x.lower() for x in el.allowed_statuses}:
        return False, f"status {s.status}"
    if s.supplier_id in el.excluded_supplier_ids:
        return False, "excluded by policy"
    if el.min_on_time_pct is not None and s.on_time_pct < el.min_on_time_pct:
        return False, f"on-time {s.on_time_pct:g}% below {el.min_on_time_pct:g}%"
    if el.min_quality_rating is not None and s.quality_rating < el.min_quality_rating:
        return False, f"quality {s.quality_rating:g} below {el.min_quality_rating:g}"
    return True, ""


@dataclass
class RankedSupplier:
    supplier_id: str
    name: str
    score: float
    po_count: int
    last_po_date: date | None
    recency: float
    has_contract: bool
    on_time_pct: float
    quality_rating: float
    reference_price: float | None
    reference_source: str  # "PO 7300123 (2026-10-12)" or "contract RC-2026-007"
    components: dict[str, float] = field(default_factory=dict)

    def why(self) -> str:
        bits = [f"{self.po_count} PO(s)" + (f", last {self.last_po_date}" if self.last_po_date else "")]
        if self.has_contract:
            bits.append("held a contract")
        bits.append(f"on-time {self.on_time_pct:g}%, quality {self.quality_rating:g}")
        return f"{self.supplier_id} score {self.score:.3f}: " + "; ".join(bits)


@dataclass
class ExcludedSupplier:
    supplier_id: str
    reason: str


class SupplierIndex:
    """Pre-indexes PO history and contracts by item for fast as-of queries."""

    def __init__(self, master: MasterData):
        self.master = master
        self.pos_by_item: dict[str, list[PurchaseRecord]] = {}
        for p in sorted(master.po_history, key=lambda p: (p.po_date, p.po_number)):
            self.pos_by_item.setdefault(p.item_code, []).append(p)
        self.contracts_by_item: dict[str, list[Contract]] = {}
        for c in master.contracts:
            for ln in c.lines:
                self.contracts_by_item.setdefault(ln.item_code, []).append(c)

    def pos_between(self, item_code: str, start: date, end: date) -> list[PurchaseRecord]:
        return [p for p in self.pos_by_item.get(item_code, []) if start <= p.po_date <= end]

    def preferred_list(self, item_code: str, on: date, params: Params) -> tuple[list[RankedSupplier], list[ExcludedSupplier]]:
        pl = params.preferred_list
        window_start = on - timedelta(days=pl.po_lookback_days)
        ref_start = on - timedelta(days=pl.reference_price_lookback_days)
        pos = self.pos_between(item_code, window_start, on)
        by_sup: dict[str, list[PurchaseRecord]] = {}
        for p in pos:
            by_sup.setdefault(p.supplier_id, []).append(p)
        contract_sups: dict[str, list[Contract]] = {}
        if pl.include_contract_suppliers:
            for c in self.contracts_by_item.get(item_code, []):
                if c.start_date <= on:  # 6.1(b): started on or before the date, even if expired
                    contract_sups.setdefault(c.supplier_id, []).append(c)

        candidates = sorted(set(by_sup) | set(contract_sups))
        excluded, rows = [], []
        for sid in candidates:
            s = self.master.suppliers.get(sid)
            ok, why = supplier_eligible(s, params)
            if not ok:
                excluded.append(ExcludedSupplier(sid, why))
                continue
            sp = by_sup.get(sid, [])
            last = sp[-1] if sp else None
            ref_pos = [p for p in self.pos_by_item.get(item_code, [])
                       if p.supplier_id == sid and ref_start <= p.po_date <= on]
            if ref_pos:
                rp = ref_pos[-1]
                ref_price, ref_src = rp.unit_price, f"PO {rp.po_number} ({rp.po_date})"
            else:
                cs = sorted(contract_sups.get(sid, []), key=lambda c: (c.start_date, c.contract_id))
                c = cs[-1] if cs else None
                ref_price = c.rate_for(item_code) if c else None
                ref_src = f"contract {c.contract_id}" if c else "none"
            recency = 0.0
            if last:
                age = (on - last.po_date).days
                for days, score in sorted(pl.recency_buckets, key=lambda b: b[0]):
                    if age <= days:
                        recency = score
                        break
            rows.append(RankedSupplier(
                supplier_id=sid, name=s.name, score=0.0, po_count=len(sp), last_po_date=last.po_date if last else None,
                recency=recency, has_contract=sid in contract_sups, on_time_pct=s.on_time_pct,
                quality_rating=s.quality_rating, reference_price=ref_price, reference_source=ref_src))

        max_n = max((r.po_count for r in rows), default=0) or 1
        w = pl.weights
        for r in rows:
            comp = {
                "frequency": r.po_count / max_n,
                "recency": r.recency,
                "contract": 1.0 if r.has_contract else 0.0,
                "on_time": r.on_time_pct / 100.0,
                "quality": r.quality_rating / 5.0,
            }
            r.components = {k: round(v, 4) for k, v in comp.items()}
            r.score = round(sum(w.get(k, 0.0) * v for k, v in comp.items()), 6)
        rows.sort(key=lambda r: (-r.score, -(r.last_po_date or date.min).toordinal(), -r.on_time_pct, r.supplier_id))
        return rows, excluded

    def category_suppliers(self, category: str, params: Params, exclude: set[str]) -> list[Supplier]:
        """6.4 top-up: eligible suppliers listing the category, best delivery and quality first."""
        out = []
        for s in self.master.suppliers.values():
            if s.supplier_id in exclude:
                continue
            if category.lower() not in {c.lower() for c in s.categories}:
                continue
            if supplier_eligible(s, params)[0]:
                out.append(s)
        out.sort(key=lambda s: (-(0.5 * s.on_time_pct / 100 + 0.5 * s.quality_rating / 5), -s.on_time_pct, s.supplier_id))
        return out
