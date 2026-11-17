from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401  (registers tables)
from . import repo
from .api import master, policy
from .policy.store import seed_default_policy
from .config import settings
from .db import Base, SessionLocal, engine

app = FastAPI(title="Corvo PR-to-PO", version="0.1.0")

Base.metadata.create_all(engine)
with SessionLocal() as _db:  # local/docker: seed master data from ./kit if the DB is empty
    repo.seed_master_from_kit(_db, settings.kit_dir)
    seed_default_policy(_db)  # policy v5.0 as the first approved version

app.include_router(master.router)
app.include_router(policy.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "llm_provider": settings.llm_provider}


# Serve the built React app (one deployable). Built files land in backend/static.
STATIC = Path(__file__).resolve().parents[1] / "static"
if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = STATIC / path
        return FileResponse(f if path and f.is_file() else STATIC / "index.html")
