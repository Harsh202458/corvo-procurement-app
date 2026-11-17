import os
import tempfile
from pathlib import Path

# Isolated DB and no auto-seeding for tests; must be set before the app is imported.
_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["KIT_DIR"] = str(_tmp / "no-kit")
os.environ["LLM_PROVIDER"] = "none"

KIT = Path(__file__).resolve().parents[2] / "kit"
