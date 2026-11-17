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
