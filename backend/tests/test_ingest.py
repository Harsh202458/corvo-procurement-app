from datetime import date, datetime

import pytest

from app.ingest import IngestError, parse_number, parse_pr_batch, parse_suppliers

PR_CSV = (
    "﻿PR No,Line,Created At,Requester,Department,Plant,Item Code,Description,Qty,UoM,"
    "Estimated Unit Price,Need By Date,Urgency,Cost Centre\n"
    "PR-1,1,2026-11-02 09:40,A,Stores,chk1,itm-1038,Hydraulic oil,\"1,270\",ltr,,2026-11-19,emergency,CC9\n"
    "PR-2,1,05-11-2026 08:40,B,IT,PUN1,,vci bag,abc,NOS,\"₹1,200.50\",,Normal,\n"
    "PR-2,1,2026-11-05 08:41,B,IT,PUN1,,dup,1,NOS,,,Normal,\n"
)


def test_pr_batch_tolerant_parsing():
    r = parse_pr_batch(PR_CSV)
    a, b, _ = r.records
    assert (a.plant, a.item_code, a.uom, a.quantity, a.urgency) == ("CHK1", "ITM-1038", "LTR", 1270.0, "Emergency")
    assert a.created_at == datetime(2026, 11, 2, 9, 40) and a.need_by_date == date(2026, 11, 19)
    assert a.extra == {"cost_centre": "CC9"}  # unknown columns are kept for policy rules
    assert b.quantity is None and b.estimated_unit_price == 1200.5 and b.created_at.day == 5
    assert any("unreadable quantity" in i for i in r.issues)
    assert any("appears 2 times" in i for i in r.issues)


def test_missing_required_columns():
    with pytest.raises(IngestError):
        parse_pr_batch("pr_no,plant\nPR-1,PUN1\n")


def test_supplier_categories_split():
    r = parse_suppliers("supplier_id,name,city,categories,status,on_time_pct,quality_rating\n"
                        "S1,X,Pune,Electrical Spares;Capital Equipment,Blocked,86,3.3\n")
    s = r.records[0]
    assert s.categories == ("Electrical Spares", "Capital Equipment") and not s.is_active


def test_parse_number():
    assert parse_number("2,00,000") == 200000 and parse_number("") is None and parse_number("x") is None
