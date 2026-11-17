"""Policy versions: read, propose (from operations or a full document), approve, reject.

Reading an amendment document or chat message into operations (LLM) is added on top of
these endpoints; this hand-edit path always works, with or without an LLM.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db
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
