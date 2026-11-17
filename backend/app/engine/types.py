"""Plain data types the engine works on. No ORM, no I/O.

The DB layer converts rows into these, and tests can build them straight from CSVs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Item:
    item_code: str
    description: str
    uom: str
    category: str
    item_type: str  # Material | Service | Capex
    standard_price: float


@dataclass(frozen=True)
class Supplier:
    supplier_id: str
    name: str
    city: str
    categories: tuple[str, ...]
    status: str  # Active | Inactive | Blocked
    on_time_pct: float
    quality_rating: float

    @property
    def is_active(self) -> bool:
        return self.status.strip().lower() == "active"


@dataclass(frozen=True)
class PurchaseRecord:
    """One historical PO line (po_history.csv)."""

    po_number: str
    po_date: date
    supplier_id: str
    item_code: str
    quantity: float
    uom: str
    unit_price: float
    plant: str
    delivered_on_time: bool | None


@dataclass(frozen=True)
class ContractLine:
    contract_id: str
    item_code: str
    description: str
    uom: str
    rate: float


@dataclass(frozen=True)
class Contract:
    contract_id: str
    supplier_id: str
    supplier_name: str
    start_date: date
    end_date: date
    value_cap: float
    consumed_value: float  # as at the register's last update; 0 for PDF-only contracts
    source: str = "register"  # register | pdf | manual
    lines: tuple[ContractLine, ...] = ()

    def rate_for(self, item_code: str) -> float | None:
        for ln in self.lines:
            if ln.item_code == item_code:
                return ln.rate
        return None


@dataclass(frozen=True)
class PRLine:
    pr_no: str
    pr_line: int
    created_at: datetime
    requester: str
    department: str
    plant: str
    item_code: str  # "" when the requester gave none
    description: str
    quantity: float | None  # None when unparseable
    uom: str
    estimated_unit_price: float | None
    need_by_date: date | None
    urgency: str  # Normal | Emergency
    extra: dict = field(default_factory=dict, hash=False, compare=False)  # unknown CSV columns, kept for rules

    @property
    def key(self) -> tuple[str, int]:
        return (self.pr_no, self.pr_line)

    @property
    def sort_key(self) -> tuple:
        # Policy 2.2: creation time, then PR number, then line number
        return (self.created_at, self.pr_no, self.pr_line)


@dataclass
class MasterData:
    """Everything the engine needs besides the PR lines and the policy."""

    items: dict[str, Item]
    suppliers: dict[str, Supplier]
    plants: dict[str, str]
    po_history: list[PurchaseRecord]
    contracts: list[Contract]
