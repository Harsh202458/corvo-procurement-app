# Corvo PR-to-PO

A web app where employees raise purchase requests by chat, and a decision engine
routes each requirement (PO or sourcing request) by applying a **versioned procurement
policy stored as data**. Uploading a policy amendment changes decisions without code changes.

> Status: Phase 0 (skeleton). See `PLAN.md` for the build plan.

## Run locally (one command)

```bash
cp .env.example .env        # then add your GEMINI_API_KEY (optional: the app works without it)
docker compose up --build   # http://localhost:8000
```

Task data is **not** in this repo. Put the kit in `./kit/` (git-ignored) or upload files through the app.

## Develop without Docker

```bash
cd backend && pip install -r requirements-dev.txt && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api)
cd backend && pytest
```

## Layout

| Path | What |
|---|---|
| `backend/app/engine/` | Pure decision engine (no DB/HTTP/LLM) |
| `backend/app/` | FastAPI API, DB models, LLM client |
| `frontend/` | React + Vite + Tailwind UI (built into `backend/static`) |
| `DECISIONS.md` | Where the policy is silent, what was decided and why |
| `APPROACH.md` | Architecture and AI tools used |

## Hosted

Deployed on Render from `render.yaml`. Link: _TBD_
