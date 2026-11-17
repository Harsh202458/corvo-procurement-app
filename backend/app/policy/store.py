"""Policy versions in the database: seed, draft, approve, reject, load for the engine."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..repo import audit
from .changes import diff_policies
from .schema import PolicyDocument, validate_policy
from .versions import ApprovedVersion

V5_PATH = Path(__file__).with_name("v5_0.json")


def default_policy_dict() -> dict:
    return json.loads(V5_PATH.read_text(encoding="utf-8"))


def _doc_json(p: PolicyDocument) -> dict:
    return p.model_dump(mode="json")


SEED_SOURCE = "Corvo_Procurement_Policy.pdf (encoded as v5_0.json)"


def seed_default_policy(db: Session) -> m.PolicyVersionRow | None:
    p = validate_policy(default_policy_dict())
    if db.scalar(select(func.count()).select_from(m.PolicyVersionRow)):
        # keep the system's own encoding of v5.0 in step with v5_0.json (it is not a user-approved change);
        # versions people created are never touched
        seed = db.scalar(select(m.PolicyVersionRow).where(m.PolicyVersionRow.created_by == "system",
                                                          m.PolicyVersionRow.source_name == SEED_SOURCE))
        if seed is not None and seed.document != _doc_json(p):
            before = {"validations": len(seed.document.get("validations", [])), "rules": len(seed.document.get("rules", []))}
            seed.document = _doc_json(p)
            audit(db, "system", "policy.seed_refresh", "policy_version", seed.id, before=before,
                  after={"validations": len(p.validations), "rules": len(p.rules)},
                  comment="system encoding of v5.0 updated from v5_0.json at startup")
            db.commit()
        return None
    row = m.PolicyVersionRow(
        version=p.version, effective_from=p.effective_from, status="approved", document=_doc_json(p),
        change_summary=[{"kind": "meta", "text": "Initial policy v5.0 loaded"}],
        source_name=SEED_SOURCE,
        created_by="system", approved_by="system (initial load)", approved_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    audit(db, "system", "policy.seed", "policy_version", row.id, after={"version": p.version})
    db.commit()
    return row


def create_draft(db: Session, document: dict, *, parent_id: int | None, by: str,
                 source_name: str | None = None, source_text: str | None = None,
                 operations: list | None = None) -> m.PolicyVersionRow:
    """Validate and store a proposed version. Raises PolicyInvalid if it does not validate."""
    p = validate_policy(document)
    parent = db.get(m.PolicyVersionRow, parent_id) if parent_id else None
    summary = diff_policies(parent.document, _doc_json(p)) if parent else [{"kind": "meta", "text": "New policy"}]
    row = m.PolicyVersionRow(
        version=p.version, effective_from=p.effective_from, status="draft", document=_doc_json(p),
        parent_id=parent_id, change_summary=summary, source_name=source_name, source_text=source_text,
        created_by=by,
    )
    db.add(row)
    db.flush()
    audit(db, by, "policy.draft", "policy_version", row.id,
          after={"version": p.version, "effective_from": p.effective_from, "changes": len(summary),
                 "operations": operations})
    return row


def approve(db: Session, version_id: int, by: str, comment: str | None = None) -> m.PolicyVersionRow:
    row = db.get(m.PolicyVersionRow, version_id)
    if row is None:
        raise LookupError("policy version not found")
    if row.status != "draft":
        raise ValueError(f"only drafts can be approved (this one is {row.status})")
    validate_policy(row.document)  # never activate an invalid document
    row.status, row.approved_by, row.approved_at = "approved", by, datetime.now(timezone.utc)
    audit(db, by, "policy.approve", "policy_version", row.id, before={"status": "draft"},
          after={"status": "approved", "version": row.version, "effective_from": row.effective_from},
          comment=comment)
    return row


def reject(db: Session, version_id: int, by: str, comment: str | None = None) -> m.PolicyVersionRow:
    row = db.get(m.PolicyVersionRow, version_id)
    if row is None:
        raise LookupError("policy version not found")
    if row.status != "draft":
        raise ValueError(f"only drafts can be rejected (this one is {row.status})")
    row.status = "rejected"
    audit(db, by, "policy.reject", "policy_version", row.id, before={"status": "draft"},
          after={"status": "rejected"}, comment=comment)
    return row


def approved_versions(db: Session) -> list[ApprovedVersion]:
    rows = db.scalars(select(m.PolicyVersionRow).where(m.PolicyVersionRow.status == "approved")
                      .order_by(m.PolicyVersionRow.approved_at, m.PolicyVersionRow.id)).all()
    return [ApprovedVersion(r.id, validate_policy(r.document), seq) for seq, r in enumerate(rows)]


def latest_approved(db: Session) -> m.PolicyVersionRow | None:
    return db.scalars(select(m.PolicyVersionRow).where(m.PolicyVersionRow.status == "approved")
                      .order_by(m.PolicyVersionRow.approved_at.desc(), m.PolicyVersionRow.id.desc())).first()
