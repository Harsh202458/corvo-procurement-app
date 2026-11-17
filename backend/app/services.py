"""Application services: run the engine on a stored batch and persist the results."""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from . import repo
from .engine.core import OUTPUT_COLUMNS, Engine, EngineResult
from .policy import store


def _plain(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def build_engine(db: Session) -> Engine:
    master = repo.master_snapshot(db)
    if not master.items:
        raise RuntimeError("master data is not loaded yet: upload the item master, suppliers, PO history and contracts")
    versions = store.approved_versions(db)
    return Engine(master, versions)


def process_batch(db: Session, batch_id: int, by: str) -> m.RunRow:
    engine = build_engine(db)
    id_lines = repo.batch_lines(db, batch_id)
    line_ids = {ln.key: i for i, ln in id_lines}
    result: EngineResult = engine.run([ln for _, ln in id_lines])
    rows = result.rows()

    used = sorted({lr.version.id for lr in result.lines} | {r.version.id for r in result.requirements})
    summary = {
        "lines": len(rows),
        "requirements": len(result.requirements),
        "purchase_orders": len(result.purchase_orders),
        "by_route": dict(Counter(r["route"] for r in rows)),
        "requirements_by_route": dict(Counter(r.route for r in result.requirements)),
        "value_by_route": {k: round(v, 2) for k, v in _value_by_route(result).items()},
        "policy_versions": [{"id": v.id, "version": v.label} for v in engine.versions if v.id in used],
        "contract_consumption": {k: round(v, 2) for k, v in result.contract_consumption.items()},
    }
    run = m.RunRow(batch_id=batch_id, run_by=by, policy_version_ids=used, summary=summary)
    db.add(run)
    db.flush()

    req_rows: dict[int, m.RequirementRow] = {}
    for r in result.requirements:
        row = m.RequirementRow(
            run_id=run.id, group_id=r.group_id, item_code=r.item_code, plant=r.plant,
            requirement_date=r.first_created, is_emergency=bool(r.facts.get("is_emergency")),
            total_quantity=float(r.facts.get("total_quantity") or 0), value_inr=round(r.value, 2),
            decision=r.decision, route=r.route, supplier_id=r.supplier_id, unit_price=r.unit_price,
            po_id=r.po_id, suggested_supplier_ids=r.suggestions, policy_version=r.version.label,
            policy_version_id=r.version.id, clause=r.clause, reason=r.reason, evidence=_plain(r.evidence),
            facts=_plain(r.facts), suggestion_notes=r.suggestion_notes,
            preferred=[_plain({**p.__dict__, "why": p.why()}) for p in r.preferred[:8]],
        )
        db.add(row)
        req_rows[r.seq] = row
    db.flush()

    for lr, out in zip(result.lines, rows):
        db.add(m.LineDecisionRow(
            run_id=run.id, pr_line_id=line_ids[lr.line.key],
            requirement_id=req_rows[lr.requirement.seq].id if lr.requirement else None,
            resolved_item_code=lr.resolved_item_code, match_method=lr.match.method, match_score=lr.match.score,
            route=out["route"], reason=out["reason"], policy_version=out["policy_version"], output=out,
        ))
    repo.audit(db, by, "batch.process", "run", run.id,
               after={"batch_id": batch_id, "lines": len(rows), "by_route": summary["by_route"],
                      "policy_versions": [v["version"] for v in summary["policy_versions"]]})
    db.commit()
    return run


def _value_by_route(result: EngineResult) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in result.requirements:
        out[r.route] = out.get(r.route, 0.0) + r.value
    return out


def run_rows(db: Session, run_id: int) -> list[dict]:
    q = (select(m.LineDecisionRow, m.PRLineRow).join(m.PRLineRow, m.LineDecisionRow.pr_line_id == m.PRLineRow.id)
         .where(m.LineDecisionRow.run_id == run_id).order_by(m.PRLineRow.id))
    return [ld.output for ld, _ in db.execute(q)]


def rows_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()
