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


def policy_impact(db: Session, document: dict, batch_id: int | None = None, limit: int = 300) -> dict:
    """Re-run a stored batch with and without a proposed version, and list what changes."""
    from .policy.schema import validate_policy
    from .policy.versions import ApprovedVersion

    if batch_id is None:
        batch_id = db.scalar(select(m.BatchRow.id).order_by(m.BatchRow.id.desc()))
    if batch_id is None:
        return {"batch_id": None, "note": "no batch uploaded yet, so there is nothing to compare"}
    master = repo.master_snapshot(db)
    if not master.items:
        return {"batch_id": batch_id, "note": "master data is not loaded yet"}
    lines = [ln for _, ln in repo.batch_lines(db, batch_id)]
    current = store.approved_versions(db)
    proposed = ApprovedVersion(-1, validate_policy(document), len(current) + 1)
    before = {(r["pr_no"], r["pr_line"]): r for r in Engine(master, current).run(lines).rows()}
    after_res = Engine(master, current + [proposed]).run(lines)
    after = {(r["pr_no"], r["pr_line"]): r for r in after_res.rows()}
    fields = ("route", "supplier_id", "unit_price", "group_value_inr", "suggested_supplier_ids", "po_id", "group_id")
    changed = []
    for k, a in after.items():
        b = before[k]
        diff = {f: (b[f], a[f]) for f in fields if str(b[f]) != str(a[f])}
        if diff.get("route") or diff.get("supplier_id") or diff.get("unit_price") or diff.get("group_value_inr") \
                or diff.get("suggested_supplier_ids"):
            changed.append({"pr_no": k[0], "pr_line": k[1], "before": {f: b[f] for f in fields},
                            "after": {f: a[f] for f in fields}, "changed": sorted(diff),
                            "policy_version_after": a["policy_version"]})
    on_new = sum(1 for r in after.values() if r["policy_version"] == proposed.label)
    eff = proposed.document.effective_from
    reqs = after_res.requirements
    note = None
    if on_new == 0 and reqs:
        first, last = min(r.requirement_date for r in reqs), max(r.requirement_date for r in reqs)
        note = (f"No requirement in this batch is dated on or after the effective date {eff} "
                f"(requirements run {first} to {last}), so this version would change nothing here.")
    return {"batch_id": batch_id, "lines": len(after), "lines_on_new_version": on_new, "effective_from": eff,
            "changed_count": len(changed), "changed": changed[:limit],
            "route_counts_before": dict(Counter(r["route"] for r in before.values())),
            "route_counts_after": dict(Counter(r["route"] for r in after.values())), "note": note}
