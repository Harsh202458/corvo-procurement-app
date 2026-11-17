"""Amendment readers. The LLM is faked here; real-model rehearsals are run by hand (see DECISIONS)."""
from datetime import date
from pathlib import Path

from app.llm import LLMUnavailable
from app.policy.reader import parse_amount, read_amendment, read_with_llm, read_with_rules
from app.policy.store import default_policy_dict

BASE = default_policy_dict()
FIX = Path(__file__).parent / "fixtures" / "amendments"
TODAY = date(2026, 11, 19)


class FakeLLM:
    name = "fake"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, system, user, schema=None):
        self.prompts.append(user)
        return self.answers.pop(0)


class DownLLM:
    name = "down"

    def complete_json(self, system, user, schema=None):
        raise LLMUnavailable("503 busy")


def test_amounts():
    assert parse_amount("3", "lakh") == 300000 and parse_amount("2,50,000", None) == 250000
    assert parse_amount("1.5", "crore") == 15000000


def test_rule_reader_on_fixture():
    p = read_with_rules((FIX / "a1_limits_plant_block.txt").read_text(encoding="utf-8"), BASE, today=TODAY)
    assert p.valid
    texts = " | ".join(c["text"] for c in p.changes)
    assert "Version: 5.0 → 5.1" in texts and "Effective from: 2026-04-01 → 2026-11-10" in texts
    assert "Spot PO limit (below, INR): 2,00,000 → 2,50,000" in texts
    assert "S014" in texts
    assert any("Chakan" in u or "CHK1" in u for u in p.unclear)  # the new rule needs a hand edit


def test_rule_reader_window_and_emergency():
    p = read_with_rules((FIX / "a2_emergency_category_window.txt").read_text(encoding="utf-8"), BASE, today=TODAY)
    texts = " | ".join(c["text"] for c in p.changes)
    assert "Emergency PO limit (at or below, INR): 5,00,000 → 3,00,000" in texts
    assert "days from the requirement's first line: 14 → 21" in texts


def test_llm_reader_repairs_invalid_answer():
    bad = {"summary": "s", "version": "5.1", "effective_from": "2026-11-10",
           "operations": [{"op": "set_param", "path": "limits.spot_po_below", "value": -1}]}
    good = {**bad, "operations": [{"op": "set_param", "path": "limits.spot_po_below", "value": 250000}]}
    llm = FakeLLM([bad, good])
    p = read_with_llm("raise spot limit", BASE, llm, today=TODAY, categories=[], plants=[])
    assert p.valid and p.method == "llm" and "repaired" in (p.llm_note or "")
    assert "must not be negative" in llm.prompts[1]


def test_llm_reader_defaults_version_and_flags_missing_date():
    ans = {"summary": "s", "version": None, "effective_from": None,
           "operations": [{"op": "set_param", "path": "limits.rfq_below", "value": 1500000}]}
    p = read_with_llm("rfq limit 15 lakh", BASE, FakeLLM([ans]), today=TODAY, categories=[], plants=[])
    assert p.valid and p.document["version"] == "5.1" and p.document["effective_from"] == "2026-11-19"
    assert any("No effective date" in a for a in p.assumptions)


def test_llm_rule_with_plant_condition_validates():
    ans = {"summary": "CHK1 services to RFQ", "version": "5.1", "effective_from": "2026-11-10", "operations": [
        {"op": "add_rule", "after": "R-5.2", "rule": {
            "id": "R-A1", "clause": "5.2A", "title": "CHK1 services need quotes",
            "when": {"all": [{"fact": "plant", "op": "eq", "value": "CHK1"},
                             {"fact": "item_type", "op": "eq", "value": "Service"}]},
            "then": {"decision": "SOURCING_REQUEST", "route": "RFQ", "supplier": "none", "suggest": 3}}}]}
    p = read_with_llm("...", BASE, FakeLLM([ans]), today=TODAY, categories=[], plants=[])
    assert p.valid and [r["id"] for r in p.document["rules"]][3] == "R-A1"


def test_falls_back_to_rules_when_llm_down():
    p = read_amendment("Version 5.1 effective 2026-11-10. The spot PO limit is 3 lakh.", BASE, DownLLM(),
                       today=TODAY, categories=[], plants=[])
    assert p.method == "rules" and p.valid and "unavailable" in p.llm_note
    assert p.document["params"]["limits"]["spot_po_below"] == 300000
