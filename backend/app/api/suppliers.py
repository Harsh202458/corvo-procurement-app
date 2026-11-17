"""Preferred supplier lists (policy 6): per item with the score breakdown, and the CSV export."""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..engine.preferred import SupplierIndex
from ..policy import store
from ..policy.versions import select_version
from .. import repo

router = APIRouter(prefix="/api/preferred-suppliers", tags=["suppliers"])

EXPORT_COLUMNS = ["item_code", "rank", "supplier_id", "reference_price"]


def _context(db: Session, as_of: date):
    master = repo.master_snapshot(db)
    if not master.items:
        raise HTTPException(409, "master data is not loaded yet")
    version = select_version(store.approved_versions(db), as_of)
    return master, SupplierIndex(master), version


def _row(rank: int, r) -> dict:
    return {"rank": rank, "supplier_id": r.supplier_id, "name": r.name, "score": round(r.score, 4),
            "components": r.components, "po_count": r.po_count, "last_po_date": r.last_po_date,
            "recency": r.recency, "has_contract": r.has_contract, "on_time_pct": r.on_time_pct,
            "quality_rating": r.quality_rating, "reference_price": r.reference_price,
            "reference_source": r.reference_source, "why": r.why()}


@router.get("")
def all_items(as_of: date = Query(default_factory=date.today), db: Session = Depends(get_db)):
    """Every item with its top suppliers (for the buyer's supplier page)."""
    master, idx, v = _context(db, as_of)
    p = v.document.params
    out = []
    for code, item in sorted(master.items.items()):
        rows, excluded = idx.preferred_list(code, as_of, p)
        out.append({"item_code": code, "description": item.description, "category": item.category,
                    "suppliers": [_row(i, r) for i, r in enumerate(rows[: p.preferred_list.export_top_n], 1)],
                    "listed": len(rows), "excluded": len(excluded)})
    return {"as_of": as_of, "policy_version": v.label, "weights": p.preferred_list.weights, "items": out}


@router.get("/export.csv")
def export(as_of: date = Query(default_factory=date.today), top: int | None = None,
           filename: str | None = None, db: Session = Depends(get_db)):
    """item_code, rank, supplier_id, reference_price for the top 3 (policy default) of every item."""
    master, idx, v = _context(db, as_of)
    p = v.document.params
    n = top or p.preferred_list.export_top_n
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    w.writeheader()
    for code in sorted(master.items):
        rows, _ = idx.preferred_list(code, as_of, p)
        for i, r in enumerate(rows[:n], 1):
            w.writerow({"item_code": code, "rank": i, "supplier_id": r.supplier_id,
                        "reference_price": "" if r.reference_price is None else round(r.reference_price, 2)})
    name = filename or f"{settings.submission_name}__preferred.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{item_code}")
def one_item(item_code: str, as_of: date = Query(default_factory=date.today), db: Session = Depends(get_db)):
    """Full ranked list for one item, with why each supplier is where it is, and who is excluded."""
    master, idx, v = _context(db, as_of)
    item = master.items.get(item_code.upper())
    if item is None:
        raise HTTPException(404, "item not found")
    p = v.document.params
    rows, excluded = idx.preferred_list(item.item_code, as_of, p)
    top_up = idx.category_suppliers(item.category, p, {r.supplier_id for r in rows})
    return {
        "item_code": item.item_code, "description": item.description, "category": item.category,
        "as_of": as_of, "policy_version": v.label,
        "method": {"weights": p.preferred_list.weights, "recency_buckets": p.preferred_list.recency_buckets,
                   "po_lookback_days": p.preferred_list.po_lookback_days,
                   "explanation": "score = sum(weight x signal); signals scaled 0..1: frequency = POs / most POs on "
                                  "this list, recency bucket, contract held (1/0), on-time % / 100, quality / 5"},
        "suppliers": [_row(i, r) for i, r in enumerate(rows, 1)],
        "excluded": [{"supplier_id": e.supplier_id, "reason": e.reason} for e in excluded],
        "category_top_up": [{"supplier_id": s.supplier_id, "name": s.name, "on_time_pct": s.on_time_pct,
                             "quality_rating": s.quality_rating} for s in top_up[:5]],
    }
