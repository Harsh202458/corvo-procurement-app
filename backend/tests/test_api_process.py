"""End-to-end through the API on the real kit (skipped without ./kit)."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

from .conftest import KIT

pytestmark = pytest.mark.skipif(not (KIT / "master_data").exists(), reason="task kit not present")
c = TestClient(app, headers={"X-User": "buyer.one"})


def test_full_flow_with_kit_and_amendment():
    md, cd = KIT / "master_data", KIT / "contracts"
    for kind, f in [("items", "item_master.csv"), ("suppliers", "supplier_master.csv"), ("plants", "plants.csv"),
                    ("po-history", "po_history.csv")]:
        assert c.post(f"/api/master-data/{kind}", files={"file": (f, (md / f).read_bytes())}).status_code == 200
    c.post("/api/master-data/contracts", files={"register_csv": ("r.csv", (cd / "contract_register.csv").read_bytes()),
                                                "lines_csv": ("l.csv", (cd / "contract_lines.csv").read_bytes())})
    for pdf in ("RC-2026-031.pdf", "RC-2026-033.pdf"):
        ex = c.post("/api/contracts/extract-pdf", files={"file": (pdf, (cd / "pdf" / pdf).read_bytes())}).json()
        assert ex["complete"], ex
        assert c.post("/api/contracts", json={**ex, "source": "pdf"}).status_code == 200

    batch = (KIT / "pr_batches" / "sample_pr_batch.csv").read_bytes()
    r = c.post("/api/process-batch", files={"file": ("sample.csv", batch)}).json()
    rows = {(d["pr_no"], d["pr_line"]): d for d in r["decisions"]}
    assert len(rows) == 186 and rows[("PR-26-00045", 1)]["route"] == "CONTRACT_PO"
    assert rows[("PR-26-00146", 1)]["route"] == "SPOT_PO"

    exp = c.get(f"/api/runs/{r['run_id']}/export.csv")
    assert exp.status_code == 200 and "harsh__run" in exp.headers["content-disposition"]
    assert len(list(csv.DictReader(io.StringIO(exp.text)))) == 186

    # amendment: spot limit down to 1 lakh from 1 Nov -> the 1,26,472 hydraulic oil requirement becomes an RFQ
    d = c.post("/api/policy/drafts", json={"operations": [
        {"op": "set_meta", "version": "5.1", "effective_from": "2026-11-01"},
        {"op": "set_param", "path": "limits.spot_po_below", "value": 100000}]}).json()
    c.post(f"/api/policy/versions/{d['id']}/approve")
    r2 = c.post(f"/api/batches/{r['batch_id']}/process").json()
    rows2 = {(x["pr_no"], x["pr_line"]): x for x in c.get(f"/api/runs/{r2['run_id']}/decisions").json()}
    assert rows2[("PR-26-00146", 1)]["route"] == "RFQ" and rows2[("PR-26-00146", 1)]["policy_version"] == "5.1"
    assert rows2[("PR-26-00088", 1)]["route"] == "SPOT_PO"  # 97,955 still below 1 lakh

    req = c.get(f"/api/runs/{r2['run_id']}/requirements").json()[0]
    full = c.get(f"/api/requirements/{req['id']}").json()
    assert full["lines"] and "facts" in full


def test_preferred_supplier_export_and_explanation():
    r = c.get("/api/preferred-suppliers/export.csv", params={"as_of": "2026-11-01"})
    assert r.status_code == 200 and 'harsh__preferred.csv' in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0]) == ["item_code", "rank", "supplier_id", "reference_price"]
    gloves = [x["supplier_id"] for x in rows if x["item_code"] == "ITM-1048"]
    assert gloves == ["S029", "S030"]
    assert max(int(x["rank"]) for x in rows) == 3
    one = c.get("/api/preferred-suppliers/ITM-1048", params={"as_of": "2026-11-01"}).json()
    assert one["suppliers"][0]["components"]["contract"] == 1.0 and one["excluded"][0]["supplier_id"] == "S031"


def test_read_amendment_text_without_ai_and_impact():
    r = c.post("/api/policy/read-text", json={"text": "Version 5.9, effective 2026-11-01: the emergency PO limit is 2 lakh.",
                                              "use_ai": False}).json()
    assert r["method"] == "rules" and r["valid"]
    assert any("2,00,000" in ch["text"] for ch in r["changes"])
    imp = r["impact"]
    assert imp["lines_on_new_version"] > 0
    flipped = [x for x in imp["changed"] if x["before"]["route"] == "EMERGENCY_PO"]
    assert flipped and all(x["after"]["route"] != "EMERGENCY_PO" or float(x["after"]["group_value_inr"]) <= 200000
                           for x in flipped)
    late = c.post("/api/policy/read-text", json={"text": "Version 5.9, effective 2027-06-01: spot PO limit 1 lakh.",
                                                 "use_ai": False}).json()
    assert late["impact"]["lines_on_new_version"] == 0 and "would change nothing" in late["impact"]["note"]


def test_read_document_upload():
    from pathlib import Path
    f = Path(__file__).parent / "fixtures" / "amendments" / "a2_emergency_category_window.txt"
    r = c.post("/api/policy/read-document", files={"file": (f.name, f.read_bytes())}, data={"use_ai": "false"}).json()
    assert r["valid"] and r["source_name"] == f.name and r["document"]["version"] == "5.2"


def test_chat_raises_a_pr_with_follow_ups():
    s = c.post("/api/chat", json={"message": "need gloves for Chakan"}).json()
    assert "Which item" in s["text"] and len(s["options"]) == 3
    sid = s["session_id"]
    for msg in ["2", "300", "in 2 weeks", "normal"]:
        s = c.post("/api/chat", json={"message": msg, "session_id": sid}).json()
    assert s["stage"] == "confirming" and "Shall I raise it?" in s["text"]
    s = c.post("/api/chat", json={"message": "yes", "session_id": sid}).json()
    assert s["stage"] == "done" and s["pr"]["pr_no"].startswith("PR-CHAT-")
    assert s["decision"]["resolved_item_code"] == "ITM-1048" and s["decision"]["route"] in ("SPOT_PO", "CONTRACT_PO", "RFQ")
    new = c.post("/api/chat", json={"message": "raise another", "session_id": sid}).json()
    assert new["session_id"] != sid


def test_buyer_approve_and_override():
    run_id = c.get("/api/runs").json()[-1]["id"]
    reqs = c.get(f"/api/runs/{run_id}/requirements").json()
    po = next(r for r in reqs if r["decision"] == "PO")
    a = c.post(f"/api/requirements/{po['id']}/approve").json()
    assert a["status"] == "approved" and a["document"]["type"] == "PO"
    assert c.post(f"/api/requirements/{po['id']}/approve").status_code == 409

    src = next(r for r in reqs if r["route"] == "RFQ")
    assert c.post(f"/api/requirements/{src['id']}/override", json={"route": "SPOT_PO", "supplier_id": "S004",
                  "unit_price": 10, "comment": "Supplier offered a better price"}).status_code == 422  # S004 Blocked
    assert c.post(f"/api/requirements/{src['id']}/override", json={"route": "RFP", "comment": "short"}).status_code == 422
    o = c.post(f"/api/requirements/{src['id']}/override",
               json={"route": "RFP", "comment": "Strategic item, Procurement wants a formal RFP"}).json()
    assert o["status"] == "overridden" and o["document"]["type"] == "RFP"
    full = c.get(f"/api/requirements/{src['id']}").json()
    assert full["override"]["comment"].startswith("Strategic") and full["route"] == "RFQ"  # original kept (8.1)
    assert [x["action"] for x in full["audit"]] == ["requirement.override"] and full["audit"][0]["actor"] == "buyer.one"
