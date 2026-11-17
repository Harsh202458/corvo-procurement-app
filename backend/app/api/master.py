"""Master data, contract register and PR batch uploads."""
from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .. import repo
from ..db import get_db
from ..ingest import (
    IngestError,
    attach_lines,
    parse_contract_lines,
    parse_contract_register,
    parse_items,
    parse_plants,
    parse_po_history,
    parse_pr_batch,
    parse_suppliers,
)
from .deps import current_user

router = APIRouter(prefix="/api", tags=["data"])

MAX_BYTES = 20 * 1024 * 1024


async def _read(f: UploadFile) -> bytes:
    data = await f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"{f.filename}: file too large")
    return data


def _apply_master(db: Session, kind: str, data: bytes) -> tuple[int, list[str]]:
    if kind == "items":
        r = parse_items(data); return repo.replace_items(db, r.records), r.issues
    if kind == "suppliers":
        r = parse_suppliers(data); return repo.replace_suppliers(db, r.records), r.issues
    if kind == "plants":
        r = parse_plants(data); return repo.replace_plants(db, r.records), r.issues
    if kind == "po-history":
        r = parse_po_history(data); return repo.replace_po_history(db, r.records), r.issues
    raise HTTPException(404, f"unknown master data kind '{kind}'")


@router.get("/master-data/summary")
def summary(db: Session = Depends(get_db)):
    return repo.counts(db)


@router.post("/master-data/contracts")
async def upload_contracts(register_csv: UploadFile = File(...), lines_csv: UploadFile = File(...),
                           db: Session = Depends(get_db), user: str = Depends(current_user)):
    """Contract register + contract lines (uploaded together so lines always match contracts)."""
    try:
        reg, ln = parse_contract_register(await _read(register_csv)), parse_contract_lines(await _read(lines_csv))
    except IngestError as e:
        raise HTTPException(422, str(e))
    n = repo.replace_contract_register(db, attach_lines(reg.records, ln.records))
    repo.audit(db, user, "master.upload", "contracts", register_csv.filename, after={"contracts": n})
    db.commit()
    return {"loaded": n, "issues": reg.issues + ln.issues, "counts": repo.counts(db)}


@router.post("/master-data/kit-zip")
async def upload_kit_zip(file: UploadFile = File(...), db: Session = Depends(get_db),
                         user: str = Depends(current_user)):
    """Load all master data from the task-kit zip in one go. Files are read in memory by
    name only; nothing is extracted to disk."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(await _read(file)))
    except zipfile.BadZipFile:
        raise HTTPException(422, "not a zip file")
    by_name = {PurePosixPath(n).name.lower(): n for n in zf.namelist() if not n.endswith("/")}
    wanted = {"items": "item_master.csv", "suppliers": "supplier_master.csv", "plants": "plants.csv",
              "po-history": "po_history.csv"}
    loaded, issues = {}, []
    try:
        for kind, fname in wanted.items():
            if fname in by_name:
                n, iss = _apply_master(db, kind, zf.read(by_name[fname]))
                loaded[kind] = n
                issues += [f"{fname}: {i}" for i in iss]
        if "contract_register.csv" in by_name and "contract_lines.csv" in by_name:
            reg = parse_contract_register(zf.read(by_name["contract_register.csv"]))
            ln = parse_contract_lines(zf.read(by_name["contract_lines.csv"]))
            loaded["contracts"] = repo.replace_contract_register(db, attach_lines(reg.records, ln.records))
            issues += reg.issues + ln.issues
    except IngestError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    if not loaded:
        raise HTTPException(422, "zip contains none of the expected master data files")
    repo.audit(db, user, "master.upload", "kit-zip", file.filename, after=loaded)
    db.commit()
    return {"loaded": loaded, "issues": issues, "counts": repo.counts(db)}


@router.post("/master-data/{kind}")
async def upload_master(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                        user: str = Depends(current_user)):
    try:
        n, issues = _apply_master(db, kind, await _read(file))
    except IngestError as e:
        raise HTTPException(422, str(e))
    repo.audit(db, user, "master.upload", kind, file.filename, after={"rows": n})
    db.commit()
    return {"loaded": n, "issues": issues, "counts": repo.counts(db)}


# ---------- PR batches ----------

@router.post("/batches")
async def upload_batch(file: UploadFile = File(...), db: Session = Depends(get_db),
                       user: str = Depends(current_user)):
    try:
        r = parse_pr_batch(await _read(file))
    except IngestError as e:
        raise HTTPException(422, str(e))
    batch = repo.create_batch(db, file.filename or "batch.csv", r.records, r.issues, "csv", user)
    db.commit()
    return {"batch_id": batch.id, "lines": len(batch.lines), "issues": r.issues}


@router.get("/batches")
def list_batches(db: Session = Depends(get_db)):
    rows = db.scalars(select(m.BatchRow).order_by(m.BatchRow.id.desc()))
    return [{"id": b.id, "name": b.name, "source": b.source, "uploaded_by": b.uploaded_by,
             "uploaded_at": b.uploaded_at, "lines": len(b.lines), "issues": b.issues} for b in rows]


@router.get("/batches/{batch_id}/lines")
def get_batch_lines(batch_id: int, db: Session = Depends(get_db)):
    if db.get(m.BatchRow, batch_id) is None:
        raise HTTPException(404, "batch not found")
    return [{"id": i, **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in ln.__dict__.items()}}
            for i, ln in sorted(repo.batch_lines(db, batch_id), key=lambda t: t[1].sort_key)]
