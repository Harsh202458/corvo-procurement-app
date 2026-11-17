"""CSV parsers: text in, engine dataclasses out. Pure (no DB).

Parsers are tolerant: headers are matched case-insensitively (with a few synonyms),
numbers may contain commas or a rupee sign, and dates accept ISO or DD-MM-YYYY.
Problems are reported as issues rather than exceptions, so a bad row never sinks a batch.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .engine.types import (
    Contract,
    ContractLine,
    Item,
    MasterData,
    PRLine,
    PurchaseRecord,
    Supplier,
)


class IngestError(ValueError):
    """The file as a whole cannot be read (e.g. required columns missing)."""


@dataclass
class ParseResult:
    records: list
    issues: list[str] = field(default_factory=list)


# ---------- low-level helpers ----------

def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


SYNONYMS = {
    "pr_number": "pr_no",
    "pr": "pr_no",
    "line": "pr_line",
    "line_no": "pr_line",
    "pr_line_no": "pr_line",
    "created": "created_at",
    "creation_time": "created_at",
    "qty": "quantity",
    "unit": "uom",
    "need_by": "need_by_date",
    "needby_date": "need_by_date",
    "est_unit_price": "estimated_unit_price",
    "estimated_price": "estimated_unit_price",
    "item": "item_code",
    "value_ceiling": "value_cap",
    "ceiling": "value_cap",
    "consumed": "consumed_value",
}


def read_rows(text_or_path: str | Path | bytes) -> list[dict[str, str]]:
    if isinstance(text_or_path, Path):
        text = text_or_path.read_text(encoding="utf-8-sig")
    elif isinstance(text_or_path, bytes):
        text = text_or_path.decode("utf-8-sig", errors="replace")
    else:
        text = text_or_path.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw in reader:
        row = {}
        for k, v in raw.items():
            if k is None:
                continue
            nk = _norm_header(k)
            nk = SYNONYMS.get(nk, nk)
            row[nk] = (v or "").strip() if isinstance(v, str) else ""
        if any(row.values()):
            rows.append(row)
    return rows


def _require(rows: list[dict], cols: list[str], what: str) -> None:
    if not rows:
        raise IngestError(f"{what}: file has no data rows")
    missing = [c for c in cols if c not in rows[0]]
    if missing:
        raise IngestError(f"{what}: missing column(s) {', '.join(missing)}")


def parse_number(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.replace(",", "").replace("₹", "").replace("INR", "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y")
_DT_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
               "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M")


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    dt = parse_datetime(s)
    return dt.date() if dt else None


def parse_datetime(s: str | None) -> datetime | None:
    """Naive datetime; all timestamps are treated as IST (see DECISIONS.md)."""
    if not s:
        return None
    s = s.strip()
    for f in _DT_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def parse_bool(s: str | None) -> bool | None:
    if s is None:
        return None
    v = s.strip().lower()
    if v in ("true", "yes", "y", "1"):
        return True
    if v in ("false", "no", "n", "0"):
        return False
    return None


# ---------- master data ----------

def parse_items(src) -> ParseResult:
    rows = read_rows(src)
    _require(rows, ["item_code", "description", "uom", "category", "item_type", "standard_price"], "item master")
    out, issues = [], []
    for i, r in enumerate(rows, start=2):
        price = parse_number(r["standard_price"])
        if not r["item_code"]:
            issues.append(f"row {i}: missing item_code, skipped")
            continue
        if price is None:
            issues.append(f"row {i} {r['item_code']}: bad standard_price '{r['standard_price']}', using 0")
        out.append(Item(r["item_code"], r["description"], r["uom"], r["category"], r["item_type"], price or 0.0))
    return ParseResult(out, issues)


def parse_suppliers(src) -> ParseResult:
    rows = read_rows(src)
    _require(rows, ["supplier_id", "name", "categories", "status"], "supplier master")
    out, issues = [], []
    for i, r in enumerate(rows, start=2):
        if not r["supplier_id"]:
            issues.append(f"row {i}: missing supplier_id, skipped")
            continue
        cats = tuple(c.strip() for c in re.split(r"[;|]", r.get("categories", "")) if c.strip())
        out.append(Supplier(
            r["supplier_id"], r["name"], r.get("city", ""), cats, r["status"] or "Active",
            parse_number(r.get("on_time_pct")) or 0.0, parse_number(r.get("quality_rating")) or 0.0,
        ))
    return ParseResult(out, issues)


def parse_plants(src) -> ParseResult:
    rows = read_rows(src)
    _require(rows, ["plant"], "plants")
    return ParseResult([(r["plant"], r.get("name", r["plant"])) for r in rows if r["plant"]])


def parse_po_history(src) -> ParseResult:
    rows = read_rows(src)
    _require(rows, ["po_number", "po_date", "supplier_id", "item_code", "unit_price"], "PO history")
    out, issues = [], []
    for i, r in enumerate(rows, start=2):
        d, price = parse_date(r["po_date"]), parse_number(r["unit_price"])
        if d is None or price is None:
            issues.append(f"row {i} PO {r['po_number']}: bad date or price, skipped")
            continue
        out.append(PurchaseRecord(
            r["po_number"], d, r["supplier_id"], r["item_code"], parse_number(r.get("quantity")) or 0.0,
            r.get("uom", ""), price, r.get("plant", ""), parse_bool(r.get("delivered_on_time")),
        ))
    return ParseResult(out, issues)


def parse_contract_register(src) -> ParseResult:
    rows = read_rows(src)
    _require(rows, ["contract_id", "supplier_id", "start_date", "end_date", "value_cap"], "contract register")
    out, issues = [], []
    for i, r in enumerate(rows, start=2):
        s, e = parse_date(r["start_date"]), parse_date(r["end_date"])
        if not (s and e):
            issues.append(f"row {i} {r['contract_id']}: bad dates, skipped")
            continue
        out.append(Contract(
            r["contract_id"], r["supplier_id"], r.get("supplier_name", ""), s, e,
            parse_number(r["value_cap"]) or 0.0, parse_number(r.get("consumed_value")) or 0.0, "register",
        ))
    return ParseResult(out, issues)


def parse_contract_lines(src) -> ParseResult:
    rows = read_rows(src)
    _require(rows, ["contract_id", "item_code", "rate"], "contract lines")
    out, issues = [], []
    for i, r in enumerate(rows, start=2):
        rate = parse_number(r["rate"])
        if rate is None:
            issues.append(f"row {i} {r['contract_id']}/{r['item_code']}: bad rate, skipped")
            continue
        out.append(ContractLine(r["contract_id"], r["item_code"], r.get("description", ""), r.get("uom", ""), rate))
    return ParseResult(out, issues)


def attach_lines(contracts: list[Contract], lines: list[ContractLine]) -> list[Contract]:
    by_id: dict[str, list[ContractLine]] = {}
    for ln in lines:
        by_id.setdefault(ln.contract_id, []).append(ln)
    from dataclasses import replace

    return [replace(c, lines=tuple(by_id.get(c.contract_id, ()))) for c in contracts]


# ---------- PR batch ----------

PR_COLUMNS = ["pr_no", "pr_line", "created_at", "requester", "department", "plant", "item_code",
              "description", "quantity", "uom", "estimated_unit_price", "need_by_date", "urgency"]
PR_REQUIRED = ["pr_no", "created_at", "plant", "quantity"]


def parse_pr_batch(src) -> ParseResult:
    """Parse a PR batch. Rows with bad values are kept (quantity/need-by None) so the
    engine can mark them NOT_PROCESSED with a reason, rather than silently dropping them."""
    rows = read_rows(src)
    _require(rows, PR_REQUIRED, "PR batch")
    if "item_code" not in rows[0] and "description" not in rows[0]:
        raise IngestError("PR batch: needs an item_code or description column")
    out, issues = [], []
    for i, r in enumerate(rows, start=2):
        created = parse_datetime(r.get("created_at"))
        if created is None:
            issues.append(f"row {i} {r.get('pr_no')}: unreadable created_at '{r.get('created_at')}', skipped")
            continue
        try:
            line_no = int(float(r.get("pr_line") or 1))
        except ValueError:
            line_no = 1
            issues.append(f"row {i} {r['pr_no']}: bad pr_line '{r.get('pr_line')}', using 1")
        qty = parse_number(r.get("quantity"))
        if qty is None:
            issues.append(f"row {i} {r['pr_no']}/{line_no}: unreadable quantity '{r.get('quantity')}'")
        need_by = parse_date(r.get("need_by_date"))
        if r.get("need_by_date") and need_by is None:
            issues.append(f"row {i} {r['pr_no']}/{line_no}: unreadable need_by_date '{r.get('need_by_date')}'")
        urgency = (r.get("urgency") or "Normal").strip()
        urgency = "Emergency" if urgency.lower().startswith("emerg") else urgency.title() or "Normal"
        extra = {k: v for k, v in r.items() if k not in PR_COLUMNS}
        out.append(PRLine(
            pr_no=r["pr_no"], pr_line=line_no, created_at=created,
            requester=r.get("requester", ""), department=r.get("department", ""),
            plant=r.get("plant", "").strip().upper(), item_code=r.get("item_code", "").strip().upper(),
            description=r.get("description", ""), quantity=qty, uom=r.get("uom", "").strip().upper(),
            estimated_unit_price=parse_number(r.get("estimated_unit_price")),
            need_by_date=need_by, urgency=urgency, extra=extra,
        ))
    seen: dict[tuple, int] = {}
    for ln in out:
        seen[ln.key] = seen.get(ln.key, 0) + 1
    for k, n in seen.items():
        if n > 1:
            issues.append(f"{k[0]}/{k[1]} appears {n} times in the file")
    return ParseResult(out, issues)


# ---------- whole kit ----------

def load_master_from_dir(kit: Path) -> tuple[MasterData, list[str]]:
    """Read master data + contract register from a kit folder (used for seeding and tests)."""
    md, cd = kit / "master_data", kit / "contracts"
    items, sups, plants, pos = (parse_items(md / "item_master.csv"), parse_suppliers(md / "supplier_master.csv"),
                                parse_plants(md / "plants.csv"), parse_po_history(md / "po_history.csv"))
    reg, lines = parse_contract_register(cd / "contract_register.csv"), parse_contract_lines(cd / "contract_lines.csv")
    issues = [*items.issues, *sups.issues, *pos.issues, *reg.issues, *lines.issues]
    data = MasterData(
        items={i.item_code: i for i in items.records},
        suppliers={s.supplier_id: s for s in sups.records},
        plants=dict(plants.records),
        po_history=pos.records,
        contracts=attach_lines(reg.records, lines.records),
    )
    return data, issues
