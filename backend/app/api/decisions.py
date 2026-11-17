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
    if full:
        out.update({"reason": r.reason, "evidence": r.evidence, "facts": r.facts, "preferred": r.preferred,
                    "suggestion_notes": r.suggestion_notes})
    else:
        out["reason"] = r.reason
    return out


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
