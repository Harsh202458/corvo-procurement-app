"""Database tables. JSON columns work on both Postgres and SQLite."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- master data ----------------

class ItemRow(Base):
    __tablename__ = "items"
    item_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    description: Mapped[str] = mapped_column(String(300))
    uom: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(100), index=True)
    item_type: Mapped[str] = mapped_column(String(40))
    standard_price: Mapped[float] = mapped_column(Float)


class SupplierRow(Base):
    __tablename__ = "suppliers"
    supplier_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100), default="")
    categories: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20))
    on_time_pct: Mapped[float] = mapped_column(Float, default=0)
    quality_rating: Mapped[float] = mapped_column(Float, default=0)


class PlantRow(Base):
    __tablename__ = "plants"
    plant: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class PoHistoryRow(Base):
    __tablename__ = "po_history"
    po_number: Mapped[str] = mapped_column(String(40), primary_key=True)
    po_date: Mapped[date] = mapped_column(Date, index=True)
    supplier_id: Mapped[str] = mapped_column(String(40), index=True)
    item_code: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    uom: Mapped[str] = mapped_column(String(20))
    unit_price: Mapped[float] = mapped_column(Float)
    plant: Mapped[str] = mapped_column(String(20))
    delivered_on_time: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class ContractRow(Base):
    __tablename__ = "contracts"
    contract_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(40), index=True)
    supplier_name: Mapped[str] = mapped_column(String(200), default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    value_cap: Mapped[float] = mapped_column(Float)
    consumed_value: Mapped[float] = mapped_column(Float, default=0)  # as at register update; 0 for PDF-only
    source: Mapped[str] = mapped_column(String(20), default="register")  # register | pdf | manual
    source_file: Mapped[str | None] = mapped_column(String(300), nullable=True)
    added_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lines: Mapped[list[ContractLineRow]] = relationship(
        back_populates="contract", cascade="all, delete-orphan", order_by="ContractLineRow.id"
    )


class ContractLineRow(Base):
    __tablename__ = "contract_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.contract_id", ondelete="CASCADE"), index=True)
    item_code: Mapped[str] = mapped_column(String(40), index=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    uom: Mapped[str] = mapped_column(String(20), default="")
    rate: Mapped[float] = mapped_column(Float)
    contract: Mapped[ContractRow] = relationship(back_populates="lines")


# ---------------- policy ----------------

class PolicyVersionRow(Base):
    """A complete policy (params + ordered rules) as JSON. Never edited once active:
    a change creates a new version with its own effective date."""

    __tablename__ = "policy_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(40))
    effective_from: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | active | superseded | rejected
    document: Mapped[dict] = mapped_column(JSON)  # the full policy JSON
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("policy_versions.id"), nullable=True)
    change_summary: Mapped[list | None] = mapped_column(JSON, nullable=True)  # plain-language list of changes
    source_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------- PRs and decisions ----------------

class BatchRow(Base):
    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(20), default="csv")  # csv | chat
    uploaded_by: Mapped[str] = mapped_column(String(100), default="system")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    lines: Mapped[list[PRLineRow]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class PRLineRow(Base):
    __tablename__ = "pr_lines"
    __table_args__ = (UniqueConstraint("batch_id", "pr_no", "pr_line", name="uq_batch_pr_line"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    pr_no: Mapped[str] = mapped_column(String(40))
    pr_line: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)  # naive, IST
    requester: Mapped[str] = mapped_column(String(100), default="")
    department: Mapped[str] = mapped_column(String(100), default="")
    plant: Mapped[str] = mapped_column(String(20))
    item_code: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(String(300), default="")
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    uom: Mapped[str] = mapped_column(String(20), default="")
    estimated_unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    need_by_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    urgency: Mapped[str] = mapped_column(String(20), default="Normal")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    batch: Mapped[BatchRow] = relationship(back_populates="lines")


class RunRow(Base):
    """One processing of a batch under the policy versions active at the time."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_by: Mapped[str] = mapped_column(String(100), default="system")
    policy_version_ids: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    requirements: Mapped[list[RequirementRow]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RequirementRow(Base):
    """A clubbed requirement and the engine's decision for it (policy sections 2 to 7)."""

    __tablename__ = "requirements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[str] = mapped_column(String(40))
    item_code: Mapped[str] = mapped_column(String(40))  # or NEW
    plant: Mapped[str] = mapped_column(String(20))
    requirement_date: Mapped[datetime] = mapped_column(DateTime)
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    total_quantity: Mapped[float] = mapped_column(Float, default=0)
    value_inr: Mapped[float] = mapped_column(Float, default=0)
    decision: Mapped[str] = mapped_column(String(30))  # PO | SOURCING_REQUEST | NOT_PROCESSED
    route: Mapped[str] = mapped_column(String(40))
    supplier_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    po_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    suggested_supplier_ids: Mapped[list] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(40))
    clause: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # contract/PO used, price source, facts
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | overridden
    facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    preferred: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ranked list with score breakdown
    suggestion_notes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    policy_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run: Mapped[RunRow] = relationship(back_populates="requirements")
    lines: Mapped[list[LineDecisionRow]] = relationship(back_populates="requirement")


class LineDecisionRow(Base):
    """One output row per PR line (brief section 4)."""

    __tablename__ = "line_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    pr_line_id: Mapped[int] = mapped_column(ForeignKey("pr_lines.id", ondelete="CASCADE"))
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=True)
    resolved_item_code: Mapped[str] = mapped_column(String(40))
    match_method: Mapped[str] = mapped_column(String(30), default="")  # code | exact | fuzzy | llm | none
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    route: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text, default="")
    policy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # the exported row, exactly
    requirement: Mapped[RequirementRow | None] = relationship(back_populates="lines")


class OverrideRow(Base):
    """A buyer override. The original decision stays on the requirement (policy 8.1)."""

    __tablename__ = "overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    new_route: Mapped[str] = mapped_column(String(40))
    new_supplier_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column(String(100))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcurementDocRow(Base):
    """A PO or sourcing request (RFQ/RFP/...) created when a buyer approves."""

    __tablename__ = "procurement_docs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_type: Mapped[str] = mapped_column(String(30))  # PO | RFQ | RFP | CAPEX_SOURCING | NEW_ITEM_SOURCING
    number: Mapped[str] = mapped_column(String(40), unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requirement_ids: Mapped[list] = mapped_column(JSON, default=list)
    total_value: Mapped[float] = mapped_column(Float, default=0)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatSessionRow(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(100), default="")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLogRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(60))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
