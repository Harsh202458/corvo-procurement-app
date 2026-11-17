from datetime import date

import pytest

from app.policy.changes import OperationError, apply_operations, diff_policies, policy_summary, rule_text
from app.policy.conditions import describe, evaluate, inr
from app.policy.schema import PolicyInvalid, validate_policy
from app.policy.store import default_policy_dict
from app.policy.versions import ApprovedVersion, select_version

V5 = default_policy_dict()
P = V5["params"]


def test_v5_validates_and_reads_in_plain_language():
    p = validate_policy(V5)
    assert p.version == "5.0" and [r.clause for r in p.rules] == ["1.2", "5.1", "5.2", "5.3", "5.4", "5.5", "5.6"]
    text = "\n".join(policy_summary(V5))
    assert "Spot PO limit (below, INR): 2,00,000" in text
    assert "value is at or below 5,00,000" in text


@pytest.mark.parametrize("cond,facts,expected", [
    ({"fact": "value", "op": "lt", "value": {"param": "limits.spot_po_below"}}, {"value": 199999.99}, True),
    ({"fact": "value", "op": "lt", "value": {"param": "limits.spot_po_below"}}, {"value": 200000}, False),
    ({"fact": "value", "op": "lte", "value": {"param": "limits.emergency_po_max"}}, {"value": 500000}, True),
    ({"fact": "item_type", "op": "eq", "value": "capex"}, {"item_type": "Capex"}, True),
    ({"fact": "plant", "op": "in", "value": ["CHK1", "PUN2"]}, {"plant": "chk1"}, True),
    ({"fact": "departments", "op": "contains", "value": "maintenance"}, {"departments": ["Maintenance", "IT"]}, True),
    ({"fact": "value", "op": "between", "value": [100, 200]}, {"value": 150}, True),
    ({"fact": "requirement_date", "op": "gte", "value": "2026-11-15"}, {"requirement_date": date(2026, 11, 15)}, True),
    ({"fact": "value", "op": "lt", "value": 10}, {"value": None}, False),
    ({"fact": "quantity", "op": "not_exists"}, {"quantity": None}, True),
    ({"not": {"fact": "is_emergency", "op": "eq", "value": True}}, {"is_emergency": False}, True),
    ({"all": []}, {}, True),
])
def test_conditions(cond, facts, expected):
    assert evaluate(cond, facts, P) is expected


def test_describe_and_inr():
    assert inr(2070780) == "20,70,780" and inr(7.0) == "7" and inr(9168.71) == "9,169" and inr(7.25) == "7.25"
    c = {"all": [{"fact": "is_emergency", "op": "eq", "value": True},
                 {"fact": "value", "op": "lte", "value": {"param": "limits.emergency_po_max"}}]}
    assert describe(c, P) == "is emergency and value is at or below 5,00,000"


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d["rules"][3]["when"]["all"].append({"fact": "colour", "op": "eq", "value": "red"}), "unknown requirement fact"),
    (lambda d: d["rules"][4]["when"]["all"].__setitem__(0, {"fact": "value", "op": "lt", "value": {"param": "limits.nope"}}), "unknown parameter"),
    (lambda d: d["rules"][2]["then"].__setitem__("supplier", "none"), "needs supplier"),
    (lambda d: d["rules"][4]["then"].__setitem__("supplier", "contract"), "must test contract_available"),
    (lambda d: d["rules"][0]["when"].__setitem__("op", "approx"), "unknown operator"),
    (lambda d: d["params"]["clubbing"].__setitem__("keys", ["item_code", "colour"]), "unsupported key"),
])
def test_validation_catches_mistakes(mutate, msg):
    import copy
    d = copy.deepcopy(V5)
    mutate(d)
    with pytest.raises(PolicyInvalid) as e:
        validate_policy(d)
    assert any(msg in err for err in e.value.errors), e.value.errors


def test_operations_and_diff():
    ops = [
        {"op": "set_meta", "version": "5.1", "effective_from": "2026-11-19"},
        {"op": "set_param", "path": "limits.spot_po_below", "value": 300000},
        {"op": "add_rule", "before": "R-5.3", "rule": {
            "id": "R-A1", "clause": "A1", "title": "CHK1 services need quotes",
            "when": {"all": [{"fact": "plant", "op": "eq", "value": "CHK1"},
                             {"fact": "item_type", "op": "eq", "value": "Service"}]},
            "then": {"decision": "SOURCING_REQUEST", "route": "RFQ", "suggest": 3}}},
    ]
    new = apply_operations(V5, ops)
    assert V5["params"]["limits"]["spot_po_below"] == 200000  # input untouched
    validate_policy(new)
    texts = [c["text"] for c in diff_policies(V5, new)]
    assert "Version: 5.0 → 5.1" in texts
    assert "Spot PO limit (below, INR): 2,00,000 → 3,00,000" in texts
    assert any(t.startswith("New rule (before R-5.3): §A1 CHK1 services need quotes") for t in texts)
    with pytest.raises(OperationError):
        apply_operations(V5, [{"op": "remove_rule", "id": "R-9.9"}])


REHEARSAL_AMENDMENTS = {
    "raise emergency limit": [{"op": "set_param", "path": "limits.emergency_po_max", "value": 750000}],
    "category-specific spot limit": [
        {"op": "set_param", "path": "limits.safety_spot_po_below", "value": 100000},
        {"op": "add_rule", "before": "R-5.4", "rule": {
            "id": "R-A2", "clause": "A2", "title": "Safety & PPE above 1 lakh needs quotes",
            "when": {"all": [{"fact": "category", "op": "eq", "value": "Safety & PPE"},
                             {"fact": "value", "op": "gte", "value": {"param": "limits.safety_spot_po_below"}}]},
            "then": {"decision": "SOURCING_REQUEST", "route": "RFQ", "suggest": 3}}}],
    "plant-only emergency rule": [{"op": "update_rule", "id": "R-5.3", "changes": {"when": {"all": [
        {"fact": "is_emergency", "op": "eq", "value": True}, {"fact": "plant", "op": "eq", "value": "PUN1"},
        {"fact": "value", "op": "lte", "value": {"param": "limits.emergency_po_max"}},
        {"fact": "bought_recently", "op": "eq", "value": True}]}}}],
    "longer clubbing window": [{"op": "set_param", "path": "clubbing.creation_window_days", "value": 30}],
    "supplier quality floor": [{"op": "set_param", "path": "supplier_eligibility.min_quality_rating", "value": 3.5}],
    "block a supplier": [{"op": "set_param", "path": "supplier_eligibility.excluded_supplier_ids", "value": ["S014"]}],
    "contracts not for emergencies": [{"op": "update_rule", "id": "R-5.2", "changes": {"when": {"all": [
        {"fact": "contract_available", "op": "eq", "value": True},
        {"fact": "is_emergency", "op": "eq", "value": False}]}}}],
    "new route for services": [{"op": "add_rule", "after": "R-5.2", "rule": {
        "id": "R-A3", "clause": "A3", "title": "Large services go to tender",
        "when": {"all": [{"fact": "item_type", "op": "eq", "value": "Service"},
                         {"fact": "value", "op": "gte", "value": 500000}]},
        "then": {"decision": "SOURCING_REQUEST", "route": "SERVICE_TENDER", "suggest": 4}}}],
    "unknown plant line check removed": [{"op": "remove_validation", "id": "V2"}],
    "extra CSV column rule": [{"op": "add_validation", "validation": {
        "id": "V9", "clause": "A4", "title": "Cost centre required",
        "when": {"fact": "pr.cost_centre", "op": "not_exists"}, "reason": "No cost centre"}}],
}


@pytest.mark.parametrize("name", list(REHEARSAL_AMENDMENTS))
def test_rehearsal_amendments_need_no_code(name):
    new = apply_operations(V5, REHEARSAL_AMENDMENTS[name])
    validate_policy(new)
    assert diff_policies(V5, new)


def test_select_version_by_date():
    v50 = validate_policy(V5)
    v51 = validate_policy(apply_operations(V5, [{"op": "set_meta", "version": "5.1", "effective_from": "2026-11-10"}]))
    v51b = validate_policy(apply_operations(V5, [{"op": "set_meta", "version": "5.1b", "effective_from": "2026-11-10"}]))
    vs = [ApprovedVersion(1, v50, 0), ApprovedVersion(2, v51, 1)]
    assert select_version(vs, date(2026, 11, 9)).id == 1
    assert select_version(vs, date(2026, 11, 10)).id == 2
    assert select_version(vs, date(2026, 1, 1)).id == 1  # before everything: earliest
    assert select_version(vs + [ApprovedVersion(3, v51b, 2)], date(2026, 11, 20)).id == 3  # tie: latest approved


def test_rule_text():
    assert rule_text(V5["rules"][2], P) == "§5.2 Rate contract can be used: if contract available → PO (CONTRACT_PO) to the contract supplier at the contract rate"
