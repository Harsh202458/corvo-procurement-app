# Corvo PR-to-PO

Employees raise purchase requests by **chatting**; a decision engine applies Corvo's procurement policy and decides,
per requirement, whether to place a **purchase order** (contract, emergency or spot PO) or start a **sourcing request**
(RFQ, RFP, capex or new-item sourcing). The policy is **stored as data**, versioned with effective dates: when Procurement
uploads an amendment, the app reads it, shows what changes and what it does to the last batch, and on approval decides
differently, with no code change.

**Hosted app:** https://corvo-pr-to-po.onrender.com (free tier: the first request after a quiet spell can take 30 to 60 s)

| Document | What is in it |
|---|---|
| [DECISIONS.md](DECISIONS.md) | Where the policy, brief or data is silent or ambiguous: what I noticed, decided and why. Includes the supplier ranking method. |
| [APPROACH.md](APPROACH.md) | Architecture, how policy-as-data works, AI tools used (building and inside the app), testing, limits. |
| [PLAN.md](PLAN.md) | The build plan this was built from. |

## Run it locally (one command)

```bash
cp .env.example .env          # optional: add GEMINI_API_KEY to enable the AI readers; the app works without it
docker compose up --build     # http://localhost:8000
```

The task data is **not** in this repository. Either put the unzipped kit in `./kit/` (git-ignored; master data is then
loaded on first start) or, in the app, go to **Procurement → Data & runs → Load the task kit (.zip)**.
Contracts that exist only as signed PDFs are added under **Buyer → Contracts → Read contract PDF**.

### Without Docker

```bash
cd backend && pip install -r requirements-dev.txt && uvicorn app.main:app --reload   # API on :8000 (SQLite)
cd frontend && npm install && npm run dev                                              # UI on :5173, proxies /api
cd backend && pytest                                                                   # 80 tests; kit-based ones skip without ./kit
```

## Using the app

Pick a role at the top right; your name is recorded on every action.

| Role | Screen | One obvious next action |
|---|---|---|
| Requester | **New request** | Chat: "need 200 nitrile gloves for Chakan by next Friday". The assistant matches the item (or offers candidates, or treats it as a new item), asks for anything missing one question at a time, confirms, raises the PR and shows the decision. |
| Requester | Upload batch | Upload a PR batch CSV. |
| Buyer | **Workspace** | Review requirements: lines, value, supplier and price or suggested suppliers, and *why* (clause, policy version, contract or PO relied on, preferred-list scores). **Approve** creates the PO / RFQ / RFP; **Override** needs a written reason. Full history per requirement. |
| Buyer | Contracts | Add a PDF-only contract: the PDF is read, you check the fields, save. |
| Buyer | Preferred suppliers | Ranked list per item as of any date, with each supplier's scores; export the top 3 as CSV. |
| Procurement | **Policy** | Propose a change (upload an amendment PDF/DOCX/TXT, or describe it). Review the computed diff and the impact on the last batch, edit if needed, save as draft, approve. |
| Procurement | Data & runs | Load master data, process and re-run batches, export decisions, blind-test checklist. |

## Blind-test runbook (Thu 19 Nov)

1. **Procurement → Data & runs**: master data loaded; **Buyer → Contracts** shows RC-2026-031 and RC-2026-033 as `pdf`.
2. **Run 1**: upload the new PR batch under *Process a PR batch* (policy v5.0 is in force). In *Runs and exports*, export it as `harsh__run1.csv`.
3. **Amendment**: **Procurement → Policy → Propose a policy change → Upload amendment document**.
   Check the **version** and **effective date** (requirements dated before it stay on v5.0), the list of changes, the
   *Not understood* box and the impact table. Fix anything in *Edit the operations (JSON)* → *Re-check*.
   **Save as draft for approval → Approve & activate**.
4. **Run 2**: back in *Data & runs*, click **Re-run under current policy** on the same batch, export as `harsh__run2.csv`.
5. **Buyer → Preferred suppliers**: set the given date, **Export top 3 as CSV** → `harsh__preferred.csv`.
6. Put the three files in `submission/` and push them (only those files; no code changes after 10:00).

If the AI service is slow or down, untick *Use AI reader*: the rule-based reader handles limits, windows, dates,
version and excluded suppliers, and lists every sentence it could not handle, to add by hand in the JSON editor.

## API

| Method and path | Purpose |
|---|---|
| `POST /api/process-batch` (multipart `file`; `?format=csv` for CSV) | Upload a PR batch and get the decisions, one row per PR line |
| `POST /api/batches/{id}/process` | Re-run a stored batch under the current policy versions |
| `GET /api/runs`, `/api/runs/{id}/decisions`, `/api/runs/{id}/export.csv?filename=` | Runs and exports |
| `GET /api/runs/{id}/requirements`, `/api/requirements/{id}` | Requirements with evidence, preferred list, documents, history |
| `POST /api/requirements/{id}/approve` · `/override` | Buyer actions (override needs `comment`) |
| `GET /api/preferred-suppliers?as_of=`, `/{item_code}`, `/export.csv?as_of=` | Preferred supplier lists |
| `GET /api/policy/versions?on=`, `/versions/{id}`, `/vocabulary` | Policy versions (which is in force on a date), the facts rules can use |
| `POST /api/policy/read-document`, `/read-text`, `/preview`, `/impact`, `/drafts`, `/versions/{id}/approve` · `/reject` | Policy change workflow |
| `POST /api/chat` | Chat intake turn |
| `POST /api/master-data/kit-zip`, `/master-data/{items,suppliers,plants,po-history}`, `/master-data/contracts` | Load master data |
| `GET /api/contracts`, `POST /api/contracts/extract-pdf`, `POST /api/contracts` | Contracts and PDF-only contracts |
| `GET /api/audit` | Audit log |

Interactive docs: `/docs` on any running instance.

Output CSV columns: `pr_no, pr_line, resolved_item_code, group_id, po_id, decision, route, supplier_id, unit_price,
group_value_inr, suggested_supplier_ids, policy_version, reason`.

## Layout

| Path | What |
|---|---|
| `backend/app/engine/` | Decision engine: pure functions (matching, clubbing, contracts, valuation, preferred list, rules, consolidation) |
| `backend/app/policy/` | Policy as data: schema and validation, condition language, fact vocabulary, versions, change operations and diffs, amendment readers, `v5_0.json` |
| `backend/app/api/` | FastAPI routes |
| `backend/app/chat.py` | Chat intake assistant |
| `backend/tests/` | Tests, including the 14 worked examples and the 3 example preferred lists |
| `frontend/src/` | React + TypeScript + Tailwind UI (built into `backend/static`, served by the API) |
| `render.yaml`, `Dockerfile`, `docker-compose.yml` | Deployment |

## Deployment

Render Blueprint (`render.yaml`): one Docker web service (API + UI) and a Postgres database. Set `GEMINI_API_KEY` in the
Render dashboard. Every push to `main` redeploys.
