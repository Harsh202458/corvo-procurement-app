"""The 14 worked examples from the kit, as regression tests (skipped without ./kit)."""
import pytest

from .conftest import KIT

pytestmark = pytest.mark.skipif(not (KIT / "master_data").exists(), reason="task kit not present")


@pytest.fixture(scope="module")
def all_rows():
    from app.contract_pdf import extract_contract
    from app.engine.core import Engine
    from app.ingest import load_master_from_dir, parse_pr_batch
    from app.policy.schema import validate_policy
    from app.policy.store import default_policy_dict
    from app.policy.versions import ApprovedVersion

    md, _ = load_master_from_dir(KIT)
    known = {c.contract_id for c in md.contracts}
    for f in sorted((KIT / "contracts" / "pdf").glob("*.pdf")):
        c = extract_contract(f.read_bytes()).to_contract()
        if c.contract_id not in known:  # PDF-only contracts (4.4)
            md.contracts.append(c)
    engine = Engine(md, [ApprovedVersion(1, validate_policy(default_policy_dict()), 0)])
    res = engine.run(parse_pr_batch(KIT / "pr_batches" / "sample_pr_batch.csv").records)
    return res.rows()


@pytest.fixture(scope="module")
def rows(all_rows):
    return {r["pr_no"]: r for r in all_rows if r["pr_line"] == 1}


def check(r, route, supplier=None, price=None, value=None, suggested=None):
    assert r["route"] == route, r["reason"]
    if supplier is not None:
        assert r["supplier_id"] == supplier
    if price is not None:
        assert round(float(r["unit_price"])) == price
    if value is not None:
        assert abs(float(r["group_value_inr"]) - value) <= 1
    if suggested is not None:
        assert r["suggested_supplier_ids"] == ";".join(suggested)


def test_01_contract_po(rows):
    check(rows["PR-26-00019"], "CONTRACT_PO", "S001", 9169, 137531)
    assert "RC-2025-011" in rows["PR-26-00019"]["reason"]


def test_02_pdf_only_contract(rows):
    check(rows["PR-26-00045"], "CONTRACT_PO", "S021", 2439, 26829)
    assert "RC-2026-031" in rows["PR-26-00045"]["reason"]


def test_03_contract_ceiling_nearly_used(rows):
    a, b = rows["PR-26-00004"], rows["PR-26-00026"]
    assert a["resolved_item_code"] == "ITM-1022" and a["group_id"] == b["group_id"]
    check(a, "RFQ", value=220256, suggested=["S014", "S013", "S016"])
    assert "RC-2026-002" in a["reason"]


def test_04_two_contracts_lowest_rate_with_room(rows):
    a, b = rows["PR-26-00034"], rows["PR-26-00032"]
    assert a["group_id"] == b["group_id"]
    check(a, "CONTRACT_PO", "S007", 7, 32547)
    assert "RC-2026-013" in a["reason"] and "RC-2026-015" in a["reason"]


def test_05_split_prs_clubbed(rows):
    ids = {rows[p]["group_id"] for p in ("PR-26-00146", "PR-26-00144", "PR-26-00145")}
    assert len(ids) == 1
    check(rows["PR-26-00146"], "SPOT_PO", "S024", 166, 126472)


def test_06_same_item_different_plant(rows):
    a, b = rows["PR-26-00106"], rows["PR-26-00103"]
    assert a["group_id"] != b["group_id"] and b["resolved_item_code"] == "ITM-1044"
    check(a, "SPOT_PO", "S027", 22, 156170)


def test_07_free_text_matched(rows):
    r = rows["PR-26-00088"]
    assert r["resolved_item_code"] == "ITM-1035"
    check(r, "SPOT_PO", "S023", 392, 97955)


def test_08_emergency_po(rows):
    check(rows["PR-26-00059"], "EMERGENCY_PO", "S014", 106, 253184)


def test_09_rfq_above_po_limit(rows):
    check(rows["PR-26-00089"], "RFQ", value=336562, suggested=["S043", "S044", "S042"])


def test_10_rfq_small_but_never_bought(rows):
    check(rows["PR-26-00050"], "RFQ", value=80300, suggested=["S003", "S002", "S005"])


def test_11_rfp(rows):
    check(rows["PR-26-00100"], "RFP", value=2070780, suggested=["S020", "S017", "S019", "S018"])


def test_12_capex(rows):
    r = rows["PR-26-00047"]
    assert r["resolved_item_code"] == "ITM-1071"
    check(r, "CAPEX_SOURCING", value=6036704, suggested=["S045", "S047", "S020", "S048", "S046"])


def test_13_new_item(rows):
    r = rows["PR-26-00170"]
    assert r["resolved_item_code"] == "NEW" and r["decision"] == "SOURCING_REQUEST"
    check(r, "NEW_ITEM_SOURCING", value=141100, suggested=[])


def test_14_several_requirements_on_one_po(rows):
    a, b, c, d = (rows[p] for p in ("PR-26-00031", "PR-26-00035", "PR-26-00034", "PR-26-00032"))
    assert a["group_id"] == b["group_id"] != c["group_id"] == d["group_id"]
    assert a["po_id"] == c["po_id"] != ""
    check(a, "CONTRACT_PO", "S007", 7, 23227)


def test_data_checks(rows):
    assert rows["PR-26-00172"]["route"] == "NOT_PROCESSED"  # quantity 0
    assert rows["PR-26-00173"]["route"] == "NOT_PROCESSED"  # plant PUN2
    assert rows["PR-26-00175"]["route"] == "NOT_PROCESSED" and "PR-26-00174/1" in rows["PR-26-00175"]["reason"]
    assert rows["PR-26-00174"]["route"] == "SPOT_PO"


def test_no_blocked_or_inactive_supplier_anywhere(rows):
    import csv
    bad = {r["supplier_id"] for r in csv.DictReader(open(KIT / "master_data" / "supplier_master.csv", encoding="utf-8-sig"))
           if r["status"] != "Active"}
    for r in rows.values():
        assert r["supplier_id"] not in bad, r
        assert not (set(r["suggested_supplier_ids"].split(";")) & bad)


def test_every_line_gets_a_row(all_rows):
    assert len(all_rows) == 186 and len({(r["pr_no"], r["pr_line"]) for r in all_rows}) == 186
