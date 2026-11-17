"""Persistence: engine dataclasses <-> DB rows, plus the audit log."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from . import models as m
from .engine.types import Contract, ContractLine, Item, MasterData, PRLine, PurchaseRecord, Supplier
from .ingest import load_master_from_dir


def _jsonable(d):
    if d is None:
        return None
    out = {}
    for k, v in d.items():
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def audit(db: Session, actor: str, action: str, entity_type: str, entity_id, *,
          before: dict | None = None, after: dict | None = None, comment: str | None = None) -> None:
    db.add(m.AuditLogRow(actor=actor, action=action, entity_type=entity_type, entity_id=str(entity_id),
                         before=_jsonable(before), after=_jsonable(after), comment=comment))


# ---------- master data writes (replace whole table: uploads are full snapshots) ----------

def replace_items(db: Session, items: list[Item]) -> int:
    db.execute(delete(m.ItemRow))
    db.add_all(m.ItemRow(**asdict(i)) for i in items)
    return len(items)


def replace_suppliers(db: Session, sups: list[Supplier]) -> int:
    db.execute(delete(m.SupplierRow))
    db.add_all(m.SupplierRow(**{**asdict(s), "categories": list(s.categories)}) for s in sups)
    return len(sups)


def replace_plants(db: Session, plants: list[tuple[str, str]]) -> int:
    db.execute(delete(m.PlantRow))
    db.add_all(m.PlantRow(plant=p, name=n) for p, n in plants)
    return len(plants)


def replace_po_history(db: Session, pos: list[PurchaseRecord]) -> int:
    db.execute(delete(m.PoHistoryRow))
    db.add_all(m.PoHistoryRow(**asdict(p)) for p in pos)
    return len(pos)


def _contract_row(c: Contract, added_by: str | None = None, source_file: str | None = None) -> m.ContractRow:
    row = m.ContractRow(
        contract_id=c.contract_id, supplier_id=c.supplier_id, supplier_name=c.supplier_name,
        start_date=c.start_date, end_date=c.end_date, value_cap=c.value_cap,
        consumed_value=c.consumed_value, source=c.source, added_by=added_by, source_file=source_file,
    )
    row.lines = [m.ContractLineRow(item_code=ln.item_code, description=ln.description, uom=ln.uom, rate=ln.rate)
                 for ln in c.lines]
    return row


def replace_contract_register(db: Session, contracts: list[Contract]) -> int:
    """Replace register contracts. PDF/manual contracts are kept unless the register now
    contains the same contract id (the register is then the newer source of truth)."""
    ids = {c.contract_id for c in contracts}
    for row in db.scalars(select(m.ContractRow).where(
            (m.ContractRow.source == "register") | (m.ContractRow.contract_id.in_(ids)))):
        db.delete(row)
    db.flush()
    db.add_all(_contract_row(c) for c in contracts)
    return len(contracts)


def upsert_contract(db: Session, c: Contract, added_by: str, source_file: str | None = None) -> m.ContractRow:
    old = db.get(m.ContractRow, c.contract_id)
    before = None
    if old is not None:
        before = {"contract_id": old.contract_id, "source": old.source, "value_cap": old.value_cap}
        db.delete(old)
        db.flush()
    row = _contract_row(c, added_by=added_by, source_file=source_file)
    db.add(row)
    audit(db, added_by, "contract.upsert", "contract", c.contract_id, before=before,
          after={"supplier_id": c.supplier_id, "start_date": c.start_date, "end_date": c.end_date,
                 "value_cap": c.value_cap, "source": c.source, "lines": len(c.lines)})
    return row


# ---------- reads ----------

def master_snapshot(db: Session) -> MasterData:
    items = {r.item_code: Item(r.item_code, r.description, r.uom, r.category, r.item_type, r.standard_price)
             for r in db.scalars(select(m.ItemRow))}
    sups = {r.supplier_id: Supplier(r.supplier_id, r.name, r.city, tuple(r.categories or ()), r.status,
                                    r.on_time_pct, r.quality_rating)
            for r in db.scalars(select(m.SupplierRow))}
    plants = {r.plant: r.name for r in db.scalars(select(m.PlantRow))}
    pos = [PurchaseRecord(r.po_number, r.po_date, r.supplier_id, r.item_code, r.quantity, r.uom,
                          r.unit_price, r.plant, r.delivered_on_time)
           for r in db.scalars(select(m.PoHistoryRow).order_by(m.PoHistoryRow.po_date, m.PoHistoryRow.po_number))]
    contracts = [
        Contract(r.contract_id, r.supplier_id, r.supplier_name, r.start_date, r.end_date, r.value_cap,
                 r.consumed_value, r.source,
                 tuple(ContractLine(r.contract_id, ln.item_code, ln.description, ln.uom, ln.rate) for ln in r.lines))
        for r in db.scalars(select(m.ContractRow).options(selectinload(m.ContractRow.lines))
                            .order_by(m.ContractRow.contract_id))
    ]
    return MasterData(items, sups, plants, pos, contracts)


def counts(db: Session) -> dict[str, int]:
    def n(model):
        return db.scalar(select(func.count()).select_from(model)) or 0

    pdf_contracts = db.scalar(select(func.count()).select_from(m.ContractRow)
                              .where(m.ContractRow.source != "register")) or 0
    return {"items": n(m.ItemRow), "suppliers": n(m.SupplierRow), "plants": n(m.PlantRow),
            "po_history": n(m.PoHistoryRow), "contracts": n(m.ContractRow),
            "contracts_pdf_or_manual": pdf_contracts, "contract_lines": n(m.ContractLineRow),
            "batches": n(m.BatchRow), "policy_versions": n(m.PolicyVersionRow)}


# ---------- PR batches ----------

def create_batch(db: Session, name: str, lines: list[PRLine], issues: list[str],
                 source: str = "csv", by: str = "system") -> m.BatchRow:
    batch = m.BatchRow(name=name, source=source, uploaded_by=by, issues=issues)
    seen = set()
    for ln in lines:
        if ln.key in seen:  # duplicate pr_no/pr_line in one file: keep the first (reported in issues)
            continue
        seen.add(ln.key)
        batch.lines.append(m.PRLineRow(
            pr_no=ln.pr_no, pr_line=ln.pr_line, created_at=ln.created_at, requester=ln.requester,
            department=ln.department, plant=ln.plant, item_code=ln.item_code, description=ln.description,
            quantity=ln.quantity, uom=ln.uom, estimated_unit_price=ln.estimated_unit_price,
            need_by_date=ln.need_by_date, urgency=ln.urgency, extra=ln.extra,
        ))
    db.add(batch)
    db.flush()
    audit(db, by, "batch.create", "batch", batch.id, after={"name": name, "lines": len(batch.lines),
                                                            "source": source})
    return batch


def batch_lines(db: Session, batch_id: int) -> list[tuple[int, PRLine]]:
    rows = db.scalars(select(m.PRLineRow).where(m.PRLineRow.batch_id == batch_id))
    return [(r.id, PRLine(r.pr_no, r.pr_line, r.created_at, r.requester, r.department, r.plant, r.item_code,
                          r.description, r.quantity, r.uom, r.estimated_unit_price, r.need_by_date, r.urgency,
                          dict(r.extra or {})))
            for r in rows]


# ---------- seeding ----------

def seed_master_from_kit(db: Session, kit_dir: Path) -> dict | None:
    """Load master data + contract register from a local kit folder if the DB is empty."""
    if db.scalar(select(func.count()).select_from(m.ItemRow)):
        return None
    if not (kit_dir / "master_data" / "item_master.csv").exists():
        return None
    data, issues = load_master_from_dir(kit_dir)
    replace_items(db, list(data.items.values()))
    replace_suppliers(db, list(data.suppliers.values()))
    replace_plants(db, list(data.plants.items()))
    replace_po_history(db, data.po_history)
    replace_contract_register(db, data.contracts)
    audit(db, "system", "master.seed", "kit", str(kit_dir), after={"issues": len(issues)})
    db.commit()
    return counts(db)
