import io
import zipfile

from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app, headers={"X-User": "tester"})

ITEMS = "item_code,description,uom,category,item_type,standard_price\nITM-1,Bolt,NOS,Fasteners,Material,7\n"
SUPS = "supplier_id,name,city,categories,status,on_time_pct,quality_rating\nS1,A,Pune,Fasteners,Active,90,4\n"
REG = "contract_id,supplier_id,supplier_name,start_date,end_date,value_cap,consumed_value\nRC-1,S1,A,2026-01-01,2026-12-31,100000,5000\n"
LINES = "contract_id,item_code,description,uom,rate\nRC-1,ITM-1,Bolt,NOS,6.5\n"
PR = ("pr_no,pr_line,created_at,requester,department,plant,item_code,description,quantity,uom,"
      "estimated_unit_price,need_by_date,urgency\nPR-1,1,2026-11-02 10:00,A,Stores,PUN1,ITM-1,Bolt,10,NOS,,2026-11-20,Normal\n")


def test_upload_master_and_contracts():
    assert c.post("/api/master-data/items", files={"file": ("i.csv", ITEMS)}).json()["loaded"] == 1
    assert c.post("/api/master-data/suppliers", files={"file": ("s.csv", SUPS)}).json()["loaded"] == 1
    r = c.post("/api/master-data/contracts", files={"register_csv": ("r.csv", REG), "lines_csv": ("l.csv", LINES)})
    assert r.status_code == 200 and r.json()["counts"]["contract_lines"] == 1
    assert c.post("/api/master-data/nope", files={"file": ("x.csv", ITEMS)}).status_code == 404
    assert c.post("/api/master-data/items", files={"file": ("x.csv", "a,b\n1,2\n")}).status_code == 422


def test_kit_zip_upload():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Kit/master_data/item_master.csv", ITEMS)
        z.writestr("Kit/contracts/contract_register.csv", REG)
        z.writestr("Kit/contracts/contract_lines.csv", LINES)
    r = c.post("/api/master-data/kit-zip", files={"file": ("kit.zip", buf.getvalue())})
    assert r.status_code == 200 and r.json()["loaded"] == {"items": 1, "contracts": 1}


def test_batch_upload_and_list():
    r = c.post("/api/batches", files={"file": ("b.csv", PR)}).json()
    assert r["lines"] == 1
    lines = c.get(f"/api/batches/{r['batch_id']}/lines").json()
    assert lines[0]["pr_no"] == "PR-1" and lines[0]["quantity"] == 10
    assert c.get("/api/batches").json()[0]["uploaded_by"] == "tester"
