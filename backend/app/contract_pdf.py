"""Extract a rate contract from its signed PDF (policy 4.4: PDF-only contracts are binding).

Regex over the text layer first (the kit's contracts follow one template); the result is
always shown to a buyer to check before it is saved. An LLM fallback can be layered on top
for documents that do not follow the template.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pdfplumber

from .engine.types import Contract, ContractLine
from .ingest import parse_date, parse_number

ITEM_ROW = re.compile(r"^\s*\d+\s+(ITM-\d+)\s+(.+?)\s+([A-Z]{2,5})\s+([\d,]+(?:\.\d+)?)\s*$", re.M)


@dataclass
class ExtractedContract:
    contract_id: str | None = None
    supplier_id: str | None = None
    supplier_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    value_cap: float | None = None
    signed_on: str | None = None
    lines: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def complete(self) -> bool:
        return all([self.contract_id, self.supplier_id, self.start_date, self.end_date, self.value_cap]) and bool(self.lines)

    def to_contract(self, source: str = "pdf") -> Contract:
        if not self.complete:
            raise ValueError("contract is incomplete: " + "; ".join(self.warnings or ["missing fields"]))
        return Contract(
            contract_id=self.contract_id, supplier_id=self.supplier_id, supplier_name=self.supplier_name or "",
            start_date=parse_date(self.start_date), end_date=parse_date(self.end_date), value_cap=self.value_cap,
            consumed_value=0.0, source=source,  # signed after the register update: starts at zero (4.2)
            lines=tuple(ContractLine(self.contract_id, ln["item_code"], ln.get("description", ""),
                                     ln.get("uom", ""), float(ln["rate"])) for ln in self.lines),
        )


def pdf_text(data: bytes) -> tuple[str, list[list[list[str]]]]:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        tables = [t for p in pdf.pages for t in (p.extract_tables() or [])]
    return text, tables


def extract_contract(data: bytes) -> ExtractedContract:
    text, tables = pdf_text(data)
    out = ExtractedContract(text=text)
    flat = re.sub(r"\s+", " ", text)
    if m := re.search(r"Contract No\.?\s*([A-Z]{2}-\d{4}-\d{3})", flat):
        out.contract_id = m.group(1)
    if m := re.search(r"Supplier ID\s*(S\d+)", flat):
        out.supplier_id = m.group(1)
    if m := re.search(r"and\s+(.+?)\s*\(Supplier ID", flat):
        out.supplier_name = re.sub(r",\s*[A-Za-z ]+$", "", m.group(1)).strip()
    if m := re.search(r"valid from\s+(\S+)\s+to\s+(\S+?)[\s(]", flat):
        out.start_date, out.end_date = m.group(1), m.group(2)
    if m := re.search(r"not exceed\s+INR\s*([\d,]+(?:\.\d+)?)", flat):
        out.value_cap = parse_number(m.group(1))
    if m := re.search(r"entered into on\s+(\d{4}-\d{2}-\d{2})", flat):
        out.signed_on = m.group(1)

    for t in tables:  # table extraction first, regex over text as fallback
        if t and t[0] and any("item" in (c or "").lower() for c in t[0]):
            for row in t[1:]:
                if len(row) >= 5 and row[1] and re.match(r"ITM-\d+", row[1].strip()):
                    out.lines.append({"item_code": row[1].strip(), "description": (row[2] or "").strip(),
                                      "uom": (row[3] or "").strip(), "rate": parse_number(row[4])})
    if not out.lines:
        out.lines = [{"item_code": a, "description": b.strip(), "uom": c, "rate": parse_number(d)}
                     for a, b, c, d in ITEM_ROW.findall(text)]

    for k in ("contract_id", "supplier_id", "start_date", "end_date", "value_cap"):
        if getattr(out, k) in (None, ""):
            out.warnings.append(f"could not read {k.replace('_', ' ')}")
    if not out.lines:
        out.warnings.append("could not read the item schedule")
    if out.start_date and out.end_date and (parse_date(out.start_date) or 0) and parse_date(out.end_date) \
            and parse_date(out.start_date) > parse_date(out.end_date):
        out.warnings.append("start date is after end date")
    return out
