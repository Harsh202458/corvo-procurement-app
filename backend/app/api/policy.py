"""Policy versions: read, propose (from operations or a full document), approve, reject.

Reading an amendment document or chat message into operations (LLM) is added on top of
these endpoints; this hand-edit path always works, with or without an LLM.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .. import services
from ..db import get_db
from ..doc_text import UnsupportedDocument, document_text
from ..llm import NullClient, get_llm
from ..policy.reader import read_amendment
from ..policy import store
from ..policy.changes import PARAM_LABELS, OperationError, apply_operations, diff_policies, policy_summary
from ..policy.conditions import OP_WORDS
from ..policy.facts import LINE_FACTS, REQUIREMENT_FACTS
from ..policy.schema import PolicyInvalid, validate_policy
from ..policy.versions import select_version
from .deps import current_user

router = APIRouter(prefix="/api/policy", tags=["policy"])


def _row_summary(r: m.PolicyVersionRow) -> dict:
    return {"id": r.id, "version": r.version, "effective_from": r.effective_from, "status": r.status,
            "parent_id": r.parent_id, "created_by": r.created_by, "created_at": r.created_at,
            "approved_by": r.approved_by, "approved_at": r.approved_at, "source_name": r.source_name,
            "changes": r.change_summary or []}


@router.get("/versions")
def list_versions(on: date | None = None, db: Session = Depends(get_db)):
    rows = db.scalars(select(m.PolicyVersionRow).order_by(m.PolicyVersionRow.id.desc())).all()
    in_force = None
    approved = store.approved_versions(db)
    if approved:
        in_force = select_version(approved, on or date.today()).id
    return {"in_force_id": in_force, "on": on or date.today(),
            "versions": [{**_row_summary(r), "in_force": r.id == in_force} for r in rows]}


@router.get("/versions/{version_id}")
def get_version(version_id: int, db: Session = Depends(get_db)):
    r = db.get(m.PolicyVersionRow, version_id)
    if r is None:
        raise HTTPException(404, "policy version not found")
    return {**_row_summary(r), "document": r.document, "summary": policy_summary(r.document),
            "source_text": r.source_text}


@router.get("/vocabulary")
def vocabulary():
    """What rules may reference: facts, operators, named parameters."""
    return {"requirement_facts": {k: {"type": t, "description": d} for k, (t, d) in REQUIREMENT_FACTS.items()},
            "line_facts": {k: {"type": t, "description": d} for k, (t, d) in LINE_FACTS.items()},
            "operators": OP_WORDS, "parameters": PARAM_LABELS}


class ProposeBody(BaseModel):
    base_version_id: int | None = None  # default: latest approved
    operations: list[dict[str, Any]] | None = None
    document: dict[str, Any] | None = None  # full replacement document
    source_name: str | None = None
    source_text: str | None = None


def _build(db: Session, body: ProposeBody) -> tuple[m.PolicyVersionRow, dict]:
    base = db.get(m.PolicyVersionRow, body.base_version_id) if body.base_version_id else store.latest_approved(db)
    if base is None:
        raise HTTPException(404, "base policy version not found")
    if body.document is not None:
        return base, body.document
    if not body.operations:
        raise HTTPException(422, "give either operations or a document")
    try:
        return base, apply_operations(base.document, body.operations)
    except OperationError as e:
        raise HTTPException(422, {"errors": [str(e)]})


@router.post("/preview")
def preview(body: ProposeBody, db: Session = Depends(get_db)):
    """Dry run: what would change, and is it valid. Nothing is saved."""
    base, doc = _build(db, body)
    try:
        p = validate_policy(doc)
        errors: list[str] = []
        doc = p.model_dump(mode="json")
    except PolicyInvalid as e:
        errors = e.errors
    return {"base_version_id": base.id, "valid": not errors, "errors": errors,
            "changes": diff_policies(base.document, doc), "document": doc}


@router.post("/drafts")
def create_draft(body: ProposeBody, db: Session = Depends(get_db), user: str = Depends(current_user)):
    base, doc = _build(db, body)
    try:
        row = store.create_draft(db, doc, parent_id=base.id, by=user, source_name=body.source_name,
                                 source_text=body.source_text, operations=body.operations)
    except PolicyInvalid as e:
        raise HTTPException(422, {"errors": e.errors})
    db.commit()
    return _row_summary(row)


class DecisionBody(BaseModel):
    comment: str | None = None


@router.post("/versions/{version_id}/approve")
def approve(version_id: int, body: DecisionBody | None = None, db: Session = Depends(get_db),
            user: str = Depends(current_user)):
    try:
        row = store.approve(db, version_id, user, body.comment if body else None)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except (ValueError, PolicyInvalid) as e:
        raise HTTPException(409, str(e))
    db.commit()
    return _row_summary(row)


@router.post("/versions/{version_id}/reject")
def reject(version_id: int, body: DecisionBody | None = None, db: Session = Depends(get_db),
           user: str = Depends(current_user)):
    try:
        row = store.reject(db, version_id, user, body.comment if body else None)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return _row_summary(row)


# ---------------------------------------------------------------- reading amendments

def _read(db: Session, text: str, base_version_id: int | None, use_ai: bool, source_name: str | None) -> dict:
    base = db.get(m.PolicyVersionRow, base_version_id) if base_version_id else store.latest_approved(db)
    if base is None:
        raise HTTPException(404, "base policy version not found")
    if not text.strip():
        raise HTTPException(422, "the amendment is empty")
    cats = sorted({c for c in db.scalars(select(m.ItemRow.category))})
    plants = sorted(db.scalars(select(m.PlantRow.plant)))
    llm = get_llm() if use_ai else NullClient()
    p = read_amendment(text, base.document, llm, today=date.today(), categories=cats, plants=plants)
    out = {"base_version_id": base.id, "base_version": base.version, "source_name": source_name,
           "source_text": text, **p.as_dict()}
    if not use_ai:
        out["llm_note"] = "AI reader switched off: rule-based reader used"
    if p.valid:
        try:
            out["impact"] = services.policy_impact(db, p.document)
        except Exception as e:  # impact is advisory; never block reading
            out["impact"] = {"note": f"impact preview failed: {e}"}
    return out


@router.post("/read-document")
async def read_document(file: UploadFile = File(...), base_version_id: int | None = Form(None),
                        use_ai: bool = Form(True), db: Session = Depends(get_db)):
    """Upload an amendment (PDF, DOCX, TXT). Returns proposed operations, the computed diff and the
    impact on the last batch. Nothing is saved until a draft is created and approved."""
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "file too large")
    try:
        text = document_text(data, file.filename or "")
    except UnsupportedDocument as e:
        raise HTTPException(422, str(e))
    return _read(db, text, base_version_id, use_ai, file.filename)


class TextBody(BaseModel):
    text: str
    base_version_id: int | None = None
    use_ai: bool = True


@router.post("/read-text")
def read_text(body: TextBody, db: Session = Depends(get_db)):
    """A change described in words (e.g. from the chat)."""
    return _read(db, body.text, body.base_version_id, body.use_ai, "chat message")


class ImpactBody(BaseModel):
    document: dict[str, Any] | None = None
    operations: list[dict[str, Any]] | None = None
    base_version_id: int | None = None
    batch_id: int | None = None


@router.post("/impact")
def impact(body: ImpactBody, db: Session = Depends(get_db)):
    """What a proposed version would change on a stored batch (default: the latest)."""
    _, doc = _build(db, ProposeBody(base_version_id=body.base_version_id, operations=body.operations,
                                    document=body.document))
    try:
        validate_policy(doc)
    except PolicyInvalid as e:
        raise HTTPException(422, {"errors": e.errors})
    return services.policy_impact(db, doc, body.batch_id)


@router.get("/versions/{version_id}/impact")
def version_impact(version_id: int, batch_id: int | None = None, db: Session = Depends(get_db)):
    r = db.get(m.PolicyVersionRow, version_id)
    if r is None:
        raise HTTPException(404, "policy version not found")
    if r.status != "draft":
        raise HTTPException(409, "impact preview is for drafts; approved versions are already in effect")
    return services.policy_impact(db, r.document, batch_id)
