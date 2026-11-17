"""Item matching (policy 1.1): item code if given, else description to the item master.

Deterministic and explainable. A description matches an item only when
  * every "spec token" in the request (anything containing a digit: 6205, 3.15, 1/2, m12x100)
    appears in the item description, and
  * the fuzzy token score is high enough and clearly ahead of the runner-up.
Otherwise the line is a new item (1.2). Never guessing is deliberate: a wrong match sends a
PO for the wrong thing, while a missed match only costs a sourcing round.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from .types import Item

# requester short forms -> item master words
ABBREVIATIONS = {
    "brg": "bearing", "brgs": "bearing", "bearings": "bearing",
    "hyd": "hydraulic", "pneu": "pneumatic", "pneum": "pneumatic",
    "rod": "electrode", "electrodes": "electrode",
    "ss304": "ss 304", "alu": "aluminium", "aluminum": "aluminium",
    "elec": "electric", "qty": "", "pcs": "", "nos": "",
    "gloves": "gloves", "glove": "gloves", "boxes": "box", "bags": "bag", "bolts": "bolt", "nuts": "nut",
    "washers": "washer", "wheels": "wheel", "shoes": "shoes", "helmets": "helmet", "cylinders": "cylinder",
    "sensors": "sensor", "valves": "valve", "pallets": "pallet", "cartridges": "cartridge",
    "inches": "inch", "in": "inch",
}

# words that say something about the request, not the item
NOISE_PATTERNS = [
    r"\burgent(ly)?\b", r"\bas per (the )?sample\b", r"\(repeat\)", r"\brepeat order\b", r"\bfor maintenance\b",
    r"\bfor production\b", r"\bfor stores\b", r"\bplease\b", r"\bpls\b", r"\basap\b", r"\brequired\b", r"\bneed(ed)?\b",
]

UNIT_SUFFIX = re.compile(r"(?<=\d)(mm|kg|kw|w|a|l|hp|t|gsm|micron|ply|cum|sqmm|inch|ton|lph)\b")


def normalize(text: str) -> str:
    s = (text or "").lower()
    s = s.replace('"', " inch ").replace("''", " inch ").replace("”", " inch ")
    for pat in NOISE_PATTERNS:
        s = re.sub(pat, " ", s)
    s = UNIT_SUFFIX.sub(r" \1", s)          # 3.15mm -> 3.15 mm, 32a -> 32 a
    s = re.sub(r"[()\[\],;:+]", " ", s)
    s = re.sub(r"(?<![\d])-|-(?![\d])", " ", s)  # keep 6205-2rs joined only between digits
    words = []
    for w in s.split():
        w = w.strip(".-")
        if not w:
            continue
        w = ABBREVIATIONS.get(w, w)
        words.extend(w.split())
    return " ".join(words)


def tokens(text: str) -> list[str]:
    """Split on separators but keep decimals (3.15) and fractions (1/2) whole."""
    out = []
    for w in normalize(text).split():
        out.extend(t for t in re.split(r"-", w) if t)
    return out


def spec_tokens(toks: list[str]) -> set[str]:
    return {t for t in toks if any(ch.isdigit() for ch in t)}


@dataclass(frozen=True)
class MatchResult:
    item_code: str | None  # None = new item
    method: str  # code | description | none
    score: float | None
    note: str
    candidates: tuple[tuple[str, float], ...] = ()


class ItemMatcher:
    def __init__(self, items: dict[str, Item], min_score: float = 82.0, min_margin: float = 4.0):
        self.items = items
        self.min_score = min_score
        self.min_margin = min_margin
        self._norm = {code: " ".join(tokens(i.description)) for code, i in items.items()}
        self._specs = {code: spec_tokens(tokens(i.description)) for code, i in items.items()}

    def rank(self, description: str, uom: str = "") -> list[tuple[str, float]]:
        q_toks = tokens(description)
        if not q_toks:
            return []
        q, q_spec = " ".join(q_toks), spec_tokens(q_toks)
        scored = []
        for code, cand in self._norm.items():
            cand_specs = self._specs[code]
            # every number the requester typed must be in the item; joined forms count (50x100 ~ 50, 100)
            cand_parts = cand_specs | {p for s in cand_specs for p in re.split(r"[x/]", s) if p}
            if not all(t in cand_specs or t in cand_parts for t in q_spec):
                continue
            score = max(fuzz.token_set_ratio(q, cand), fuzz.token_sort_ratio(q, cand))
            if q_spec and q_spec <= cand_specs:
                score += 3  # exact spec agreement
            if uom and self.items[code].uom.upper() == uom.upper():
                score += 2
            elif uom:
                score -= 5
            scored.append((code, score))  # uncapped so ties near 100 still separate
        scored.sort(key=lambda x: -x[1])
        return scored

    def match(self, item_code: str, description: str, uom: str = "") -> MatchResult:
        code = (item_code or "").strip().upper()
        if code and code in self.items:
            return MatchResult(code, "code", 100.0, "item code given by requester")
        prefix = f"item code {code} not in item master; " if code else ""
        ranked = self.rank(description, uom)
        top = tuple(ranked[:3])
        if not ranked:
            return MatchResult(None, "none", None, prefix + "no item master item has the same specification", top)
        best_code, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if best >= self.min_score and best - second >= self.min_margin:
            return MatchResult(best_code, "description", round(min(best, 100.0), 1),
                               prefix + f"description matched to {best_code} ({self.items[best_code].description}), score {best:.0f}",
                               top)
        why = "score too low" if best < self.min_score else f"too close to call vs {ranked[1][0]}"
        return MatchResult(None, "none", round(min(best, 100.0), 1),
                           prefix + f"no confident match (best {best_code} score {best:.0f}, {why})", top)
