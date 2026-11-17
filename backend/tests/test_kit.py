"""Sanity checks against the real task kit, skipped when ./kit is absent (it is never committed)."""
import pytest

from app.ingest import load_master_from_dir, parse_pr_batch

from .conftest import KIT

pytestmark = pytest.mark.skipif(not (KIT / "master_data").exists(), reason="task kit not present")


def test_kit_master_loads_cleanly():
    data, issues = load_master_from_dir(KIT)
    assert issues == []
    assert len(data.items) == 73 and len(data.suppliers) == 48 and len(data.contracts) == 10
    assert all(c.lines for c in data.contracts)


def test_sample_batch_parses():
    r = parse_pr_batch(KIT / "pr_batches" / "sample_pr_batch.csv")
    assert len(r.records) == 186 and r.issues == []
