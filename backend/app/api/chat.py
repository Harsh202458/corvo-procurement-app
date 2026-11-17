"""Chat intake API: one session per PR conversation."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models as m
from .. import repo, services
from ..chat import ChatAssistant, ChatState
from ..db import get_db
from ..engine.types import PRLine
from ..llm import NullClient, get_fast_llm
from ..policy.conditions import inr
from .deps import current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None, second=0, microsecond=0)


class ChatIn(BaseModel):
    message: str
    session_id: int | None = None
    use_ai: bool = True


def _decision_text(d: dict) -> str:
    route = d["route"]
    if d["decision"] == "PO":
        return (f"Decision preview: {route.replace('_', ' ')} to {d['supplier_id']} at ₹{inr(float(d['unit_price']))} "
                f"per unit (requirement value ₹{inr(float(d['group_value_inr']))}). A buyer will review and approve it.")
    if d["decision"] == "SOURCING_REQUEST":
        sug = d["suggested_supplier_ids"].replace(";", ", ") or "the buyer will find suppliers"
        return (f"Decision preview: {route.replace('_', ' ')} (quotes needed), value ₹{inr(float(d['group_value_inr']))}. "
                f"Suggested suppliers: {sug}.")
    return f"This line could not be processed: {d['reason']}"


@router.post("")
def chat(body: ChatIn, db: Session = Depends(get_db), user: str = Depends(current_user),
         x_department: str | None = Header(default=None)):
    master = repo.master_snapshot(db)
    if not master.items:
        raise HTTPException(409, "master data is not loaded yet")
    sess = db.get(m.ChatSessionRow, body.session_id) if body.session_id else None
    if sess is None or (sess.state or {}).get("stage") == "done":
        sess = m.ChatSessionRow(user=user, department=(x_department or "")[:100], state={}, messages=[])
        db.add(sess)
        db.flush()
    state = ChatState.load(sess.state)
    assistant = ChatAssistant(master.items, master.plants, llm=get_fast_llm() if body.use_ai else NullClient(),
                              today=now_ist().date())
    reply = assistant.handle(body.message, state)
    out = {"session_id": sess.id, "text": reply.text, "options": reply.options, "stage": reply.state.stage,
           "summary": reply.summary, "pr": None, "decision": None}
    if reply.ready_to_create:
        s = reply.summary
        pr_no = f"PR-CHAT-{sess.id:05d}"
        line = PRLine(pr_no=pr_no, pr_line=1, created_at=now_ist(), requester=user, department=sess.department,
                      plant=s["plant"], item_code=s["item_code"], description=s["description"] or "",
                      quantity=float(s["quantity"]), uom=s["uom"], estimated_unit_price=s["estimated_unit_price"],
                      need_by_date=date.fromisoformat(s["need_by_date"]), urgency=s["urgency"])
        batch = repo.create_batch(db, f"chat {pr_no}", [line], [], "chat", user)
        db.commit()
        run = services.process_batch(db, batch.id, user)
        decision = services.run_rows(db, run.id)[0]
        reply.state.stage, reply.state.pr_no = "done", pr_no
        sess = db.get(m.ChatSessionRow, sess.id)
        sess.batch_id, sess.run_id = batch.id, run.id
        out.update({"text": f"Raised {pr_no}. " + _decision_text(decision), "stage": "done",
                    "pr": {"pr_no": pr_no, "batch_id": batch.id, "run_id": run.id}, "decision": decision,
                    "options": ["Raise another request"]})
    sess.state = reply.state.dump()
    sess.messages = [*sess.messages, {"role": "user", "text": body.message},
                     {"role": "assistant", "text": out["text"], "options": out["options"]}]
    sess.updated_at = datetime.now(timezone.utc)
    db.commit()
    return out


@router.get("/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db)):
    s = db.get(m.ChatSessionRow, session_id)
    if s is None:
        raise HTTPException(404, "chat session not found")
    return {"id": s.id, "user": s.user, "state": s.state, "messages": s.messages, "batch_id": s.batch_id,
            "run_id": s.run_id}
