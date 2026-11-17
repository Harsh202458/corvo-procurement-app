from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app, headers={"X-User": "procurement.head"})

OPS = [{"op": "set_meta", "version": "5.1", "effective_from": "2026-11-15"},
       {"op": "set_param", "path": "limits.spot_po_below", "value": 250000}]


def test_policy_lifecycle():
    vs = c.get("/api/policy/versions").json()
    v50 = next(v for v in vs["versions"] if v["version"] == "5.0")
    assert v50["status"] == "approved"

    pv = c.post("/api/policy/preview", json={"operations": OPS}).json()
    assert pv["valid"] and any("2,50,000" in ch["text"] for ch in pv["changes"])

    bad = c.post("/api/policy/drafts", json={"operations": [
        {"op": "set_param", "path": "limits.spot_po_below", "value": -5}]})
    assert bad.status_code == 422 and "must not be negative" in str(bad.json())

    d = c.post("/api/policy/drafts", json={"operations": OPS, "source_name": "amendment.pdf"}).json()
    assert d["status"] == "draft" and d["parent_id"] == v50["id"]
    assert c.get("/api/policy/versions", params={"on": "2026-11-20"}).json()["in_force_id"] == v50["id"]

    a = c.post(f"/api/policy/versions/{d['id']}/approve", json={"comment": "ok"}).json()
    assert a["status"] == "approved" and a["approved_by"] == "procurement.head"
    assert c.post(f"/api/policy/versions/{d['id']}/approve").status_code == 409

    assert c.get("/api/policy/versions", params={"on": "2026-11-20"}).json()["in_force_id"] == d["id"]
    assert c.get("/api/policy/versions", params={"on": "2026-11-14"}).json()["in_force_id"] == v50["id"]

    full = c.get(f"/api/policy/versions/{d['id']}").json()
    assert full["document"]["params"]["limits"]["spot_po_below"] == 250000
    assert any("2,50,000" in line for line in full["summary"])


def test_vocabulary():
    v = c.get("/api/policy/vocabulary").json()
    assert "contract_available" in v["requirement_facts"] and "plant_known" in v["line_facts"]
