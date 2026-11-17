"""Batch processing, runs, exports and contracts."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import models as m
from .. import repo, services
from ..config import settings
from ..contract_pdf import extract_contract
from ..db import get_db
from ..engine.types import Contract, ContractLine
from ..ingest import IngestError, parse_date, parse_pr_batch
from .deps import current_user
from .master import _read

router = APIRouter(prefix="/api", tags=["decisions"])


def _csv_response(text: str, filename: str) -> Response:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", filename) or "decisions.csv"
    return Response(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@router.post("/process-batch")
async def process_batch(file: UploadFile = File(...), format: str = Query("json", pattern="^(json|csv)$"),
                        db: Session = Depends(get_db), user: str = Depends(current_user)):
    """Upload a PR batch CSV and get the decisions (JSON, or CSV with ?format=csv)."""
    try:
        parsed = parse_pr_batch(await _read(file))
    except IngestError as e:
        raise HTTPException(422, str(e))
    batch = repo.create_batch(db, file.filename or "batch.csv", parsed.records, parsed.issues, "csv", user)
    db.commit()
    try:
        run = services.process_batch(db, batch.id, user)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    rows = services.run_rows(db, run.id)
    if format == "csv":
        return _csv_response(services.rows_to_csv(rows), f"decisions_run{run.id}.csv")
    return {"batch_id": batch.id, "run_id": run.id, "issues": parsed.issues, "summary": run.summary, "decisions": rows}


@router.post("/batches/{batch_id}/process")
def reprocess(batch_id: int, db: Session = Depends(get_db), user: str = Depends(current_user)):
    """Re-run a stored batch under the currently approved policy versions (e.g. after an amendment)."""
    if db.get(m.BatchRow, batch_id) is None:
        raise HTTPException(404, "batch not found")
    try:
        run = services.process_batch(db, batch_id, user)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"run_id": run.id, "summary": run.summary}


@router.get("/runs")
def list_runs(batch_id: int | None = None, db: Session = Depends(get_db)):
    q = select(m.RunRow).order_by(m.RunRow.id.desc())
    if batch_id:
        q = q.where(m.RunRow.batch_id == batch_id)
    return [{"id": r.id, "batch_id": r.batch_id, "batch_name": db.get(m.BatchRow, r.batch_id).name,
             "run_at": r.run_at, "run_by": r.run_by, "summary": r.summary} for r in db.scalars(q)]


@router.get("/runs/{run_id}/decisions")
def run_decisions(run_id: int, db: Session = Depends(get_db)):
    if db.get(m.RunRow, run_id) is None:
        raise HTTPException(404, "run not found")
    return services.run_rows(db, run_id)


@router.get("/runs/{run_id}/export.csv")
def export_run(run_id: int, filename: str | None = None, db: Session = Depends(get_db)):
    if db.get(m.RunRow, run_id) is None:
        raise HTTPException(404, "run not found")
    return _csv_response(services.rows_to_csv(services.run_rows(db, run_id)),
                         filename or f"{settings.submission_name}__run{run_id}.csv")


@router.get("/runs/{run_id}/requirements")
def run_requirements(run_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(m.RequirementRow).where(m.RequirementRow.run_id == run_id)
                      .options(selectinload(m.RequirementRow.lines)).order_by(m.RequirementRow.requirement_date)).all()
    return [_req(r, db) for r in rows]


@router.get("/requirements/{req_id}")
def requirement(req_id: int, db: Session = Depends(get_db)):
    r = db.get(m.RequirementRow, req_id)
    if r is None:
        raise HTTPException(404, "requirement not found")
    return _req(r, db, full=True)


def _req(r: m.RequirementRow, db: Session, full: bool = False) -> dict:
    lines = []
    for ld in r.lines:
        pl = db.get(m.PRLineRow, ld.pr_line_id)
        lines.append({"pr_no": pl.pr_no, "pr_line": pl.pr_line, "created_at": pl.created_at, "plant": pl.plant,
                      "requester": pl.requester, "department": pl.department, "item_code": pl.item_code,
                      "description": pl.description, "quantity": pl.quantity, "uom": pl.uom,
                      "need_by_date": pl.need_by_date, "urgency": pl.urgency,
                      "match_method": ld.match_method, "match_score": ld.match_score})
    out = {"id": r.id, "run_id": r.run_id, "group_id": r.group_id, "item_code": r.item_code, "plant": r.plant,
           "requirement_date": r.requirement_date, "is_emergency": r.is_emergency, "total_quantity": r.total_quantity,
           "value_inr": r.value_inr, "decision": r.decision, "route": r.route, "supplier_id": r.supplier_id,
           "unit_price": r.unit_price, "po_id": r.po_id, "suggested_supplier_ids": r.suggested_supplier_ids,
           "policy_version": r.policy_version, "clause": r.clause, "status": r.status, "lines": lines}
    out["reason"] = r.reason
    ov = db.scalars(select(m.OverrideRow).where(m.OverrideRow.requirement_id == r.id).order_by(m.OverrideRow.id)).all()
    out["override"] = ({"route": ov[-1].new_route, "supplier_id": ov[-1].new_supplier_id,
                        "unit_price": ov[-1].new_unit_price, "comment": ov[-1].comment, "by": ov[-1].by,
                        "at": ov[-1].at} if ov else None)
    if full:
        docs = [d for d in db.scalars(select(m.ProcurementDocRow).where(m.ProcurementDocRow.run_id == r.run_id))
                if r.id in (d.requirement_ids or [])]
        trail = db.scalars(select(m.AuditLogRow).where(m.AuditLogRow.entity_type == "requirement",
                                                       m.AuditLogRow.entity_id == str(r.id))
                           .order_by(m.AuditLogRow.at)).all()
        siblings = []
        if r.po_id:
            siblings = [x.group_id for x in db.scalars(select(m.RequirementRow).where(
                m.RequirementRow.run_id == r.run_id, m.RequirementRow.po_id == r.po_id)) if x.id != r.id]
        out.update({"evidence": r.evidence, "facts": r.facts, "preferred": r.preferred,
                    "suggestion_notes": r.suggestion_notes, "po_shared_with": siblings,
                    "documents": [{"type": d.doc_type, "number": d.number, "supplier_id": d.supplier_id,
                                   "total_value": d.total_value, "created_by": d.created_by,
                                   "created_at": d.created_at} for d in docs],
                    "audit": [{"at": a.at, "actor": a.actor, "action": a.action, "comment": a.comment,
                               "before": a.before, "after": a.after} for a in trail]})
    return out


# ---------------------------------------------------------------- buyer actions

SOURCING_ROUTES = {"RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"}


def _make_doc(db: Session, r: m.RequirementRow, route: str, supplier_id: str | None, value: float, user: str):
    if supplier_id:  # a PO: requirements consolidated on one PO share the document (7.1)
        number = f"R{r.run_id}-{r.po_id}" if (r.po_id and route == r.route) else f"R{r.run_id}-PO-{r.group_id}"
        doc_type = "PO"
    else:
        number, doc_type = f"R{r.run_id}-{route}-{r.group_id}", route
    doc = db.scalar(select(m.ProcurementDocRow).where(m.ProcurementDocRow.number == number))
    if doc is None:
        doc = m.ProcurementDocRow(doc_type=doc_type, number=number, run_id=r.run_id, supplier_id=supplier_id,
                                  requirement_ids=[], total_value=0, created_by=user)
        db.add(doc)
    if r.id not in (doc.requirement_ids or []):
        doc.requirement_ids = [*(doc.requirement_ids or []), r.id]
        doc.total_value = round((doc.total_value or 0) + value, 2)
    return doc


class ApproveIn(BaseModel):
    comment: str | None = None


@router.post("/requirements/{req_id}/approve")
def approve_requirement(req_id: int, body: ApproveIn | None = None, db: Session = Depends(get_db),
                        user: str = Depends(current_user)):
    r = db.get(m.RequirementRow, req_id)
    if r is None:
        raise HTTPException(404, "requirement not found")
    if r.status != "pending":
        raise HTTPException(409, f"requirement is already {r.status}")
    if r.decision == "NOT_PROCESSED":
        raise HTTPException(409, "a not-processed requirement cannot be approved; override it with a reason")
    value = (r.unit_price or 0) * r.total_quantity if r.supplier_id else r.value_inr
    doc = _make_doc(db, r, r.route, r.supplier_id, value, user)
    r.status = "approved"
    repo.audit(db, user, "requirement.approve", "requirement", r.id, before={"status": "pending"},
               after={"status": "approved", "route": r.route, "supplier_id": r.supplier_id, "document": doc.number},
               comment=body.comment if body else None)
    db.commit()
    return {"status": r.status, "document": {"type": doc.doc_type, "number": doc.number}}


class OverrideIn(BaseModel):
    route: str
    supplier_id: str | None = None
    unit_price: float | None = None
    comment: str


@router.post("/requirements/{req_id}/override")
def override_requirement(req_id: int, body: OverrideIn, db: Session = Depends(get_db),
                         user: str = Depends(current_user)):
    """Buyer decides differently. A written reason is required and the original decision is kept (8.1)."""
    r = db.get(m.RequirementRow, req_id)
    if r is None:
        raise HTTPException(404, "requirement not found")
    if r.status != "pending":
        raise HTTPException(409, f"requirement is already {r.status}")
    comment = (body.comment or "").strip()
    if len(comment) < 10:
        raise HTTPException(422, "an override needs a written reason (at least 10 characters)")
    route = body.route.strip().upper()
    po_routes = {"CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO"}
    if route not in po_routes | SOURCING_ROUTES | {"NOT_PROCESSED"}:
        raise HTTPException(422, f"unknown route {route}")
    sup_id = None
    if route in po_routes:
        sup = db.get(m.SupplierRow, (body.supplier_id or "").strip().upper())
        if sup is None:
            raise HTTPException(422, "a PO override needs a supplier from the supplier master")
        if sup.status != "Active":
            raise HTTPException(422, f"{sup.supplier_id} is {sup.status}: blocked or inactive suppliers never get a PO (5.7)")
        if not body.unit_price or body.unit_price <= 0:
            raise HTTPException(422, "a PO override needs a unit price")
        sup_id = sup.supplier_id
    ov = m.OverrideRow(requirement_id=r.id, new_route=route, new_supplier_id=sup_id,
                       new_unit_price=body.unit_price if sup_id else None, comment=comment, by=user)
    db.add(ov)
    doc = None
    if route != "NOT_PROCESSED":
        value = body.unit_price * r.total_quantity if sup_id else r.value_inr
        doc = _make_doc(db, r, route, sup_id, value, user)
    r.status = "overridden"
    repo.audit(db, user, "requirement.override", "requirement", r.id,
               before={"route": r.route, "supplier_id": r.supplier_id, "unit_price": r.unit_price},
               after={"route": route, "supplier_id": sup_id, "unit_price": ov.new_unit_price,
                      "document": doc.number if doc else None}, comment=comment)
    db.commit()
    return {"status": r.status, "document": {"type": doc.doc_type, "number": doc.number} if doc else None}


@router.get("/audit")
def audit_log(entity_type: str | None = None, entity_id: str | None = None, limit: int = 200,
              db: Session = Depends(get_db)):
    q = select(m.AuditLogRow).order_by(m.AuditLogRow.at.desc()).limit(min(limit, 1000))
    if entity_type:
        q = q.where(m.AuditLogRow.entity_type == entity_type)
    if entity_id:
        q = q.where(m.AuditLogRow.entity_id == entity_id)
    return [{"at": a.at, "actor": a.actor, "action": a.action, "entity_type": a.entity_type,
             "entity_id": a.entity_id, "before": a.before, "after": a.after, "comment": a.comment}
            for a in db.scalars(q)]


@router.get("/documents")
def documents(run_id: int | None = None, db: Session = Depends(get_db)):
    q = select(m.ProcurementDocRow).order_by(m.ProcurementDocRow.id.desc())
    if run_id:
        q = q.where(m.ProcurementDocRow.run_id == run_id)
    return [{"type": d.doc_type, "number": d.number, "run_id": d.run_id, "supplier_id": d.supplier_id,
             "requirement_ids": d.requirement_ids, "total_value": d.total_value, "created_by": d.created_by,
             "created_at": d.created_at} for d in db.scalars(q)]


# ---------------------------------------------------------------- contracts

@router.get("/contracts")
def list_contracts(db: Session = Depends(get_db)):
    rows = db.scalars(select(m.ContractRow).options(selectinload(m.ContractRow.lines)).order_by(m.ContractRow.contract_id))
    return [{"contract_id": c.contract_id, "supplier_id": c.supplier_id, "supplier_name": c.supplier_name,
             "start_date": c.start_date, "end_date": c.end_date, "value_cap": c.value_cap,
             "consumed_value": c.consumed_value, "source": c.source, "added_by": c.added_by,
             "lines": [{"item_code": ln.item_code, "description": ln.description, "uom": ln.uom, "rate": ln.rate}
                       for ln in c.lines]} for c in rows]


@router.post("/contracts/extract-pdf")
async def extract_pdf(file: UploadFile = File(...)):
    """Read a signed contract PDF. Nothing is saved: the buyer checks the fields, then saves."""
    data = await _read(file)
    if not data.startswith(b"%PDF"):
        raise HTTPException(422, "not a PDF")
    e = extract_contract(data)
    return {"contract_id": e.contract_id, "supplier_id": e.supplier_id, "supplier_name": e.supplier_name,
            "start_date": e.start_date, "end_date": e.end_date, "value_cap": e.value_cap, "consumed_value": 0,
            "signed_on": e.signed_on, "lines": e.lines, "warnings": e.warnings, "complete": e.complete,
            "source_file": file.filename}


class ContractLineIn(BaseModel):
    item_code: str
    description: str = ""
    uom: str = ""
    rate: float


class ContractIn(BaseModel):
    contract_id: str
    supplier_id: str
    supplier_name: str = ""
    start_date: str
    end_date: str
    value_cap: float
    consumed_value: float = 0
    source: str = "pdf"
    source_file: str | None = None
    lines: list[ContractLineIn]


@router.post("/contracts")
def save_contract(body: ContractIn, db: Session = Depends(get_db), user: str = Depends(current_user)):
    s, e = parse_date(body.start_date), parse_date(body.end_date)
    errors = []
    if not (s and e):
        errors.append("start and end dates must be valid dates")
    elif s > e:
        errors.append("start date is after end date")
    if body.value_cap <= 0:
        errors.append("value ceiling must be positive")
    if not body.lines:
        errors.append("a contract needs at least one item")
    sup = db.get(m.SupplierRow, body.supplier_id)
    if sup is None:
        errors.append(f"supplier {body.supplier_id} is not in the supplier master")
    unknown = [ln.item_code for ln in body.lines if db.get(m.ItemRow, ln.item_code) is None]
    if unknown:
        errors.append(f"items not in the item master: {', '.join(unknown)}")
    if errors:
        raise HTTPException(422, {"errors": errors})
    c = Contract(body.contract_id.strip(), body.supplier_id, body.supplier_name or sup.name, s, e, body.value_cap,
                 body.consumed_value, body.source if body.source in ("pdf", "manual") else "manual",
                 tuple(ContractLine(body.contract_id, ln.item_code, ln.description, ln.uom, ln.rate) for ln in body.lines))
    repo.upsert_contract(db, c, user, body.source_file)
    db.commit()
    warn = [] if sup.status == "Active" else [f"supplier {sup.supplier_id} is {sup.status}: this contract will not be used (4.1)"]
    return {"saved": c.contract_id, "warnings": warn}
