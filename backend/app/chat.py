"""Chat intake: turn a conversation into one confirmed PR line.

The assistant fills slots (item, quantity, plant, need-by date, urgency, and an estimated
price for new items), asks for what is missing one question at a time, never invents an
item (it offers item-master candidates or treats the request as a new item), confirms, and
only then creates the PR. Slot extraction uses the LLM when available and regexes otherwise;
item matching is always the deterministic matcher.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from dateutil.relativedelta import FR, MO, SA, SU, TH, TU, WE, relativedelta

from .engine.matching import ItemMatcher
from .engine.types import Item
from .ingest import parse_date
from .llm import LLMClient, LLMUnavailable

PLANT_WORDS = {"PUN1": ["pun1", "bhosari", "pune plant", "pune"], "CHK1": ["chk1", "chakan"]}
UOM_WORDS = {
    "NOS": ["nos", "no", "pcs", "pc", "pieces", "piece", "units", "unit", "numbers", "number", "each"],
    "KG": ["kg", "kgs", "kilo", "kilos", "kilograms"], "LTR": ["ltr", "ltrs", "litre", "litres", "liter", "liters", "l"],
    "MTR": ["mtr", "mtrs", "m", "metre", "metres", "meter", "meters"], "PAIR": ["pair", "pairs", "prs"],
    "SET": ["set", "sets", "kit", "kits"], "ROL": ["roll", "rolls", "rol"], "REAM": ["ream", "reams"],
    "CYL": ["cyl", "cylinder", "cylinders"], "BKT": ["bkt", "bucket", "buckets"], "BRL": ["brl", "barrel", "barrels"],
    "JOB": ["job", "jobs"], "MON": ["month", "months", "mon"], "QTR": ["quarter", "quarters", "qtr"],
}
WEEKDAYS = {"monday": MO, "tuesday": TU, "wednesday": WE, "thursday": TH, "friday": FR, "saturday": SA, "sunday": SU,
            "mon": MO, "tue": TU, "wed": WE, "thu": TH, "fri": FR, "sat": SA, "sun": SU}
YES = re.compile(r"^\s*(y|yes|yeah|yep|ok|okay|confirm|confirmed|go ahead|sure|correct|right|raise it|submit|done)\b", re.I)
NO = re.compile(r"^\s*(n|no|nope|cancel|stop|wrong)\b", re.I)

SLOT_ORDER = ["item", "quantity", "plant", "need_by_date", "urgency", "estimated_unit_price"]


@dataclass
class ChatState:
    stage: str = "collecting"  # collecting | choosing_item | confirming | done
    item_code: str | None = None
    is_new_item: bool = False
    item_text: str | None = None
    quantity: float | None = None
    uom: str | None = None
    plant: str | None = None
    need_by_date: str | None = None
    urgency: str | None = None
    urgency_hint: bool = False  # the user said "urgent" but has not confirmed Emergency
    estimated_unit_price: float | None = None
    candidates: list[dict] = field(default_factory=list)
    asked: str | None = None
    pr_no: str | None = None

    @classmethod
    def load(cls, d: dict | None) -> "ChatState":
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s

    def dump(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Reply:
    text: str
    state: ChatState
    options: list[str] = field(default_factory=list)  # quick-reply buttons
    ready_to_create: bool = False
    summary: dict | None = None


# ---------------------------------------------------------------- extraction

def resolve_date(text: str, today: date) -> date | None:
    t = text.lower()
    if m := re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", t):
        d = parse_date(m.group(1))
        if d:
            return d
    if m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b(?:\s+(\d{4}))?", t):
        year = int(m.group(3)) if m.group(3) else today.year
        d = parse_date(f"{int(m.group(1))} {m.group(2).title()} {year}") or None
        if d and not m.group(3) and d < today:
            d = d.replace(year=d.year + 1)
        if d:
            return d
    if m := re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", t):
        d = parse_date(f"{int(m.group(2))} {m.group(1).title()} {today.year}")
        if d and d < today:
            d = d.replace(year=d.year + 1)
        if d:
            return d
    if "today" in t:
        return today
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if "tomorrow" in t:
        return today + timedelta(days=1)
    if m := re.search(r"\bin\s+(\d+)\s+(day|days|week|weeks)\b", t):
        n = int(m.group(1))
        return today + (timedelta(weeks=n) if "week" in m.group(2) else timedelta(days=n))
    if m := re.search(r"\b(next|this|coming)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b", t):
        wd = WEEKDAYS[m.group(2)]
        d = today + relativedelta(days=1, weekday=wd(+1))  # the coming one, never today
        if m.group(1) == "next" and (d - today).days < 7 and today.weekday() < wd.weekday:
            # "next Friday" said on a Monday-Thursday means Friday of next week (common Indian usage)
            d += timedelta(days=7)
        return d
    if "next week" in t:
        return today + timedelta(days=7)
    if "end of month" in t or "month end" in t:
        return (today.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
    return None


MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
DATE_PHRASES = [
    r"\b\d{4}-\d{2}-\d{2}\b", r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTHS}(?:\s+\d{{4}})?\b", rf"\b{MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b",
    r"\bin\s+\d+\s+(?:days?|weeks?)\b", r"\b(?:day after )?tomorrow\b", r"\btoday\b",
    r"\b(?:next|this|coming)?\s*(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\bnext week\b", r"\b(?:end of (?:the )?month|month end)\b",
]
FILLER = (r"(?i)\b(i|we|need|needs|want|require|required|requirement|please|pls|kindly|order|raise|a|an|the|pr|for|"
          r"by|to|of|at|on|plant|urgent|urgently|asap|emergency|normal|routine|before|within|delivered|deliver|"
          r"delivery|send|get|me|us|our|some|it|its|is|make|change|instead|actually|quantity|qty|units?)\b")
EDIT_WORDS = re.compile(r"\b(make it|change|instead|actually|update|qty|quantity)\b", re.I)


def regex_extract(text: str, today: date, spec_numbers: set[str] | None = None) -> dict:
    """Pull slots out of free text. Numbers that belong to an item spec (6308, 50x100, 3.15mm) or to a
    date are never taken as the quantity."""
    out: dict[str, Any] = {}
    low = " " + text.lower().replace("’", "'") + " "
    if m := re.search(r"\b(ITM-\d{4})\b", text, re.I):
        out["item_code"] = m.group(1).upper()
    for plant, words in PLANT_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
            out["plant"] = plant
            break
    d = resolve_date(text, today)
    if d:
        out["need_by_date"] = d.isoformat()
    if re.search(r"\b(emergency|breakdown|line down|machine down|critical)\b", low):
        out["urgency"] = "Emergency"
    elif re.search(r"\b(urgent|urgently|asap|immediately|rush)\b", low):
        out["urgency_hint"] = True
    elif re.search(r"\b(normal|not urgent|routine|regular)\b", low):
        out["urgency"] = "Normal"
    work = low
    if pm := re.search(r"(?:₹|rs\.?\s*|inr\s*)([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:rupees|rs\b|inr\b)", low):
        out["estimated_unit_price"] = float((pm.group(1) or pm.group(2)).replace(",", ""))
        work = work.replace(pm.group(0), " ")
    work = re.sub(r"\bitm-\d{4}\b", " ", work)
    for pat in DATE_PHRASES:
        work = re.sub(pat, " ", work)
    uom_alt = "|".join(sorted({re.escape(w) for ws in UOM_WORDS.values() for w in ws}, key=len, reverse=True))
    spec = spec_numbers or set()
    qty_span = None
    for qm in re.finditer(rf"(?<![\w.\-/x])(\d[\d,]*(?:\.\d+)?)\s*({uom_alt})\b", work):  # "40 nos", "250 m"
        out["quantity"] = float(qm.group(1).replace(",", ""))
        out["uom"] = next(u for u, ws in UOM_WORDS.items() if qm.group(2) in ws)
        qty_span = qm.group(0)
        break
    if "quantity" not in out:  # a bare number that is not part of a spec
        for qm in re.finditer(r"(?<![\w.\-/])(\d[\d,]*(?:\.\d+)?)(?![\w.\-/%])", work):
            n = qm.group(1)
            if n.replace(",", "") in spec:
                continue
            out["quantity"], out["bare_quantity"] = float(n.replace(",", "")), True
            qty_span = qm.group(0)
            break
    if qty_span:
        work = work.replace(qty_span, " ", 1)
    for words in PLANT_WORDS.values():
        for w in words:
            work = re.sub(rf"\b{re.escape(w)}\b", " ", work)
    item = re.sub(FILLER, " ", work)
    item = re.sub(r"'s\b", " ", item)
    item = re.sub(r"\s+", " ", item).strip(" ,.-:;!?'")
    if len(item) >= 3 and re.search(r"[a-z]{2}", item):
        out["item_text"] = item
    return out


LLM_SYSTEM = """You extract purchase-request details from a requester's chat message for Corvo Engineering.
Return JSON only, with only the fields the message actually states (omit the rest):
{"item_text": "what they want, as written, without quantity/plant/date words",
 "item_code": "ITM-xxxx if given", "quantity": number, "uom": "NOS|KG|LTR|MTR|PAIR|SET|ROL|REAM|CYL|BKT|BRL|JOB|MON|QTR",
 "plant": "PUN1 (Bhosari/Pune) or CHK1 (Chakan)", "need_by_date": "YYYY-MM-DD",
 "urgency": "Emergency only if they say emergency/breakdown/line down; Normal if they say normal/not urgent",
 "urgency_hint": true if they only say urgent/asap,
 "estimated_unit_price": number in INR per unit,
 "choice": the option number they picked if they answer a numbered question,
 "confirm": true/false if they answer a yes/no confirmation}
Resolve relative dates against TODAY. "next Friday" means the Friday of next week if today is Monday to Thursday.
Never guess an item code. Never invent values."""


def llm_extract(text: str, llm: LLMClient, today: date, asked: str | None) -> dict:
    user = f"TODAY: {today.isoformat()} ({today.strftime('%A')})\nTHE ASSISTANT LAST ASKED: {asked or 'nothing'}\nMESSAGE: {text}"
    out = llm.complete_json(LLM_SYSTEM, user)
    return out if isinstance(out, dict) else {}


# ---------------------------------------------------------------- the conversation

class ChatAssistant:
    def __init__(self, items: dict[str, Item], plants: dict[str, str], matcher: ItemMatcher | None = None,
                 llm: LLMClient | None = None, today: date | None = None):
        self.items, self.plants = items, plants
        self.matcher = matcher or ItemMatcher(items)
        self.llm = llm
        self.today = today or date.today()
        from .engine.matching import spec_tokens, tokens

        self.spec_numbers = {p for i in items.values() for t in spec_tokens(tokens(i.description))
                             for p in re.split(r"[x/\-]", t) if p.replace(".", "").isdigit()}

    def _extract(self, text: str, state: ChatState) -> dict:
        base = regex_extract(text, self.today, self.spec_numbers)
        short_answer = len(text.split()) <= 4 and state.asked not in (None, "item")
        if self.llm is not None and not short_answer:  # short answers are parsed locally: keeps chat fast
            try:
                got = llm_extract(text, self.llm, self.today, state.asked)
                base.update({k: v for k, v in got.items() if v not in (None, "", [])})
            except LLMUnavailable:
                pass
        return base

    def _item_label(self, code: str) -> str:
        i = self.items[code]
        return f"{code} {i.description} ({i.uom})"

    def handle(self, text: str, state: ChatState) -> Reply:
        text = (text or "").strip()
        if state.stage == "done":
            state = ChatState()
        if not text:
            return Reply("Tell me what you need, e.g. \"need 200 nitrile gloves for Chakan by next Friday\".", state)

        # --- answers to a specific question
        if state.stage == "choosing_item":
            pick = None
            if m := re.fullmatch(r"\s*(?:option\s*)?(\d)\s*\.?\s*", text):
                pick = int(m.group(1))
            elif re.search(r"\b(none|new item|not listed|something else|neither)\b", text, re.I):
                pick = len(state.candidates) + 1
            else:
                for i, c in enumerate(state.candidates, 1):
                    if c["item_code"].lower() in text.lower():
                        pick = i
            if pick is not None:
                if 1 <= pick <= len(state.candidates):
                    state.item_code = state.candidates[pick - 1]["item_code"]
                    state.is_new_item = False
                else:
                    state.is_new_item, state.item_code = True, None
                state.candidates, state.stage = [], "collecting"
                return self._next(state, ack=self._ack_item(state))
            # anything else: treat as a fresh description of the item
            state.candidates, state.stage, state.item_code, state.item_text = [], "collecting", None, None

        if state.stage == "confirming":
            if YES.search(text):
                return Reply("", state, ready_to_create=True, summary=self.summary(state))
            if NO.search(text) and len(text) < 12:
                state.stage = "collecting"
                return Reply("No problem. What should I change? (item, quantity, plant, need-by date or urgency)", state)
            state.stage = "collecting"  # treat as an edit, then re-confirm
            if re.fullmatch(r"\s*(change|edit|modify)( something| it| details)?\s*\.?\s*", text, re.I):
                return Reply("Sure. What should I change? For example \"make it 300\", \"Chakan instead\", "
                             "\"by 5 Dec\" or \"it is an emergency\".", state)

        if state.asked == "urgency" and re.search(r"\b(yes|emergency)\b", text, re.I) and state.urgency_hint:
            state.urgency, state.urgency_hint = "Emergency", False
        elif state.asked == "urgency" and re.search(r"\b(no|normal)\b", text, re.I):
            state.urgency, state.urgency_hint = "Normal", False

        got = self._extract(text, state)
        # bare answers to the last question ("250", "Chakan", "next friday")
        if state.asked == "quantity" and "quantity" not in got and "need_by_date" not in got \
                and (m := re.search(r"\d[\d,]*(?:\.\d+)?", text)):
            got["quantity"] = float(m.group(0).replace(",", ""))
        if state.asked == "estimated_unit_price" and "estimated_unit_price" not in got and \
                (m := re.search(r"\d[\d,]*(?:\.\d+)?", text)):
            got["estimated_unit_price"] = float(m.group(0).replace(",", ""))
        if state.asked in ("quantity", "plant", "need_by_date", "urgency", "estimated_unit_price", "confirm"):
            got.pop("item_text", None)  # an answer to a question is not a new item
        if (state.item_code or state.is_new_item) and got.get("item_text") and not got.get("item_code"):
            # mid-conversation: only switch item if the text clearly names another one
            m2 = self.matcher.match("", got["item_text"], "")
            if not (m2.item_code and m2.item_code != state.item_code) and not re.search(r"\b(item|instead of)\b", text, re.I):
                got.pop("item_text")
        if got.get("bare_quantity") and state.quantity and state.asked != "quantity" and not EDIT_WORDS.search(text):
            got.pop("quantity")  # a stray number is not a new quantity unless the user says so

        notes: list[str] = []
        if code := got.get("item_code"):
            if code in self.items:
                state.item_code, state.is_new_item, state.item_text = code, False, None
            else:
                notes.append(f"I can't find item code {code} in the item master, so I'll go by the description.")
        if got.get("item_text") and (not state.item_code or got.get("item_text") != state.item_text):
            if not got.get("item_code") or got.get("item_code") not in self.items:
                state.item_text, state.item_code, state.is_new_item = got["item_text"], None, False
        for k in ("quantity", "uom", "plant", "estimated_unit_price"):
            if got.get(k) not in (None, ""):
                setattr(state, k, got[k])
        if got.get("need_by_date"):
            d = parse_date(str(got["need_by_date"]))
            if d and d < self.today:
                notes.append(f"{d.isoformat()} is in the past, so I haven't used it as the need-by date.")
            elif d:
                state.need_by_date = d.isoformat()
        if got.get("urgency") in ("Emergency", "Normal"):
            state.urgency, state.urgency_hint = got["urgency"], False
        elif got.get("urgency_hint") and not state.urgency:
            state.urgency_hint = True
        if state.plant and state.plant.upper() in self.plants:
            state.plant = state.plant.upper()
        elif state.plant:
            notes.append(f"I don't know plant '{state.plant}'. Corvo has {', '.join(self.plants)}.")
            state.plant = None

        # resolve the item if we have text but no code
        if state.item_text and not state.item_code and not state.is_new_item:
            m = self.matcher.match("", state.item_text, state.uom or "")
            if m.item_code:
                state.item_code = m.item_code
                notes.append(f"I've matched that to {self._item_label(m.item_code)}.")
            else:
                cands = [c for c in self.matcher.rank(state.item_text, "") if c[1] >= 45][:3]
                if cands:
                    state.candidates = [{"item_code": c, "label": self._item_label(c)} for c, _ in cands]
                    state.stage, state.asked = "choosing_item", "item"
                    opts = [f"{i}. {c['label']}" for i, c in enumerate(state.candidates, 1)]
                    opts.append(f"{len(cands) + 1}. None of these: it's a new item")
                    return Reply(" ".join(notes + [f"Which item do you mean by \"{state.item_text}\"?"]), state,
                                 options=opts)
                state.is_new_item = True
                notes.append(f"\"{state.item_text}\" isn't in the item master, so this will be a new-item request "
                             "(it goes to sourcing; Procurement finds suppliers).")
        return self._next(state, ack=" ".join(notes))

    def _ack_item(self, state: ChatState) -> str:
        if state.item_code:
            return f"Got it: {self._item_label(state.item_code)}."
        return f"OK, \"{state.item_text}\" will be raised as a new item (it goes to sourcing)."

    def missing(self, state: ChatState) -> list[str]:
        miss = []
        if not state.item_code and not state.is_new_item:
            miss.append("item")
        if not state.quantity or state.quantity <= 0:
            miss.append("quantity")
        if not state.plant:
            miss.append("plant")
        if not state.need_by_date:
            miss.append("need_by_date")
        if not state.urgency:
            miss.append("urgency")
        if state.is_new_item and not state.estimated_unit_price:
            miss.append("estimated_unit_price")
        return miss

    def _next(self, state: ChatState, ack: str = "") -> Reply:
        miss = self.missing(state)
        pre = (ack + " ") if ack else ""
        if state.item_code and state.uom and state.uom != self.items[state.item_code].uom and "quantity" not in miss:
            item_uom = self.items[state.item_code].uom
            q = f"This item is bought in {item_uom}, but you said {state.uom}. How many {item_uom} do you need?"
            state.uom, state.quantity, state.asked = None, None, "quantity"
            return Reply(pre + q, state)
        if not miss:
            state.stage, state.asked = "confirming", "confirm"
            s = self.summary(state)
            lines = [f"Item: {s['item']}", f"Quantity: {s['quantity']:g} {s['uom']}", f"Plant: {s['plant_name']}",
                     f"Need by: {s['need_by_date']}", f"Urgency: {s['urgency']}"]
            if state.is_new_item:
                lines.append(f"Estimated price: ₹{state.estimated_unit_price:,.2f} per unit")
            return Reply(pre + "Here's your request:\n" + "\n".join(lines) + "\nShall I raise it?", state,
                         options=["Yes, raise it", "Change something"], summary=s)
        slot = miss[0]
        state.asked = slot
        unit = self.items[state.item_code].uom if state.item_code else (state.uom or "units")
        questions = {
            "item": ("What do you need? An item code (ITM-xxxx) or a description is fine.", []),
            "quantity": (f"How many {unit} do you need?", []),
            "plant": ("Which plant is it for?", [f"{p} ({n})" for p, n in self.plants.items()]),
            "need_by_date": ("By when do you need it?", ["In 1 week", "In 2 weeks", "End of month"]),
            "urgency": (("You mentioned it's urgent. Should I mark it as an Emergency? Emergencies can skip quotes "
                         "up to the emergency limit, so use it only for breakdowns or line stoppages.")
                        if state.urgency_hint else "Is this a normal request or an emergency?",
                        ["Normal", "Emergency"]),
            "estimated_unit_price": ("Since it's a new item, what's your estimated price per unit (INR)?", []),
        }
        q, opts = questions[slot]
        return Reply(pre + q, state, options=opts)

    def summary(self, state: ChatState) -> dict:
        if state.item_code:
            i = self.items[state.item_code]
            item, uom, desc = f"{i.item_code} {i.description}", i.uom, i.description
        else:
            item, uom, desc = f"NEW: {state.item_text}", (state.uom or "NOS"), state.item_text
        return {"item": item, "item_code": state.item_code or "", "description": desc, "uom": uom,
                "quantity": state.quantity, "plant": state.plant, "plant_name": f"{state.plant} ({self.plants.get(state.plant, '')})",
                "need_by_date": state.need_by_date, "urgency": state.urgency or "Normal",
                "estimated_unit_price": state.estimated_unit_price, "is_new_item": state.is_new_item}
