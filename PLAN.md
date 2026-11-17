# Corvo PR-to-PO: Phase-wise Build Plan

**Today:** Tue 17 Nov · **Code freeze:** Thu 19 Nov 10:00 IST (~40 working hours) · **Blind test:** 10:00 to 12:00 Thu

Scoring: Decisions 40 · Chat/UX 20 · Policy-as-data 15 · DECISIONS.md 15 · Engineering 10.
**Priority order: get the engine right, then make the policy fully data-driven, then chat, then polish.** The blind test checks whether the engine is correct and whether amendments work. A good-looking UI on a wrong engine still loses.

---

## Recommended stack (keeps hosting simple)

| Layer | Choice | Why |
|---|---|---|
| Backend + engine | **Python FastAPI** + SQLAlchemy | Engine stays a pure, testable module (`pytest`) |
| DB | **Postgres** (SQLite for local/tests) | Free on Render, Neon or Railway |
| Frontend | **React + Vite + Tailwind** (shadcn optional) | Build once and FastAPI serves it as static files, so there is **one deployable** |
| LLM | Claude API (Sonnet 5.5 for chat and policy parsing) | Turns free-text chat into structured slots, and turns amendment text into a policy JSON patch |
| PDF | `pdfplumber` | Contract PDF extraction (regex first, LLM fallback) |
| Deploy | Docker → **Render** (one web service + Postgres) | `docker compose up` locally |

Golden rule: **the LLM never decides routes.** It only extracts structure (chat slots, policy diffs, contract fields). The deterministic engine decides. Say this in APPROACH.md.

---

## Phase 0: Setup (Tue, 1–2 h)
- Public GitHub repo. Add `.gitignore` for `data/`, `*.csv` and `*.pdf` from the kit (**the repo must not contain the task data**). Load the kit locally from a git-ignored `./kit/` folder, or upload it through the app.
- Skeleton: `backend/engine/`, `backend/api/`, `frontend/`, `docker-compose.yml`, `README.md`, `DECISIONS.md`, `APPROACH.md`.
- **Deploy a hello-world to Render now**, not on Wednesday night.
- Commit small and often. They read the commit history.

## Phase 1: Data model + loaders (Tue, 2–3 h)
Tables:
- `items`, `suppliers`, `plants`, `po_history`, `contracts` (with `source = register|pdf`, `consumed_value_at_load`), `contract_lines`
- `policy_versions` (id, version, effective_from, status `draft|active|superseded`, `rules_json`, source_doc, change_summary, approved_by, approved_at)
- `batches`, `pr_lines`, `requirements`, `requirement_lines`, `decisions` (all output columns plus `clause`, `policy_version_id` and an `evidence` JSON), `purchase_orders`, `sourcing_requests`
- `audit_log` (who, when, action, entity, before, after, comment)
- `preferred_supplier_snapshots` (optional cache)

Loaders: CSV import for master data, register and batch. Normalise dates, use IST, strip BOMs.

## Phase 2: Policy as data, schema first (Tue evening, 3–4 h) ⚠ this is the critical design
Encode v5.0 as JSON. **Nothing in the engine may hard-code a number, window or rule order.**

```jsonc
{
  "version": "5.0", "effective_from": "2026-04-01",
  "params": {
    "clubbing": { "creation_window_days": 14, "needby_window_days": 7, "keys": ["item_code","plant"] },
    "valuation_price_order": ["contract_rate","last_po_365d","standard_price"],
    "history_lookback_days": 365,
    "preferred_list": { "po_lookback_days": 548, "include_expired_contracts": true },
    "consolidation": { "window_hours": 24, "keys": ["supplier_id","plant","route"] },
    "never_use_supplier_status": ["Blocked","Inactive"]
  },
  "rules": [   // evaluated in order, first match wins
    { "id":"5.1", "when": {"fact":"item_type","op":"eq","value":"Capex"},
      "then": {"decision":"SOURCING_REQUEST","route":"CAPEX_SOURCING","suggest":5} },
    { "id":"5.2", "when": {"fact":"contract_available","op":"eq","value":true},
      "then": {"decision":"PO","route":"CONTRACT_PO","supplier":"contract"} },
    { "id":"5.3", "when": {"all":[{"fact":"is_emergency","op":"eq","value":true},
                                  {"fact":"value","op":"lte","value":500000},
                                  {"fact":"bought_active_365d","op":"eq","value":true}]},
      "then": {"decision":"PO","route":"EMERGENCY_PO","supplier":"preferred_1"} },
    ...
  ]
}
```
- **Facts the engine exposes** (the vocabulary amendments can use): every PR field (plant, department, requester, uom, urgency, qty), item fields (item_type, category, item_code), derived facts (value, is_new_item, is_emergency, contract_available, bought_active_365d, days_to_need_by, line_count, preferred_count, supplier on-time/quality). **Expose more facts than v5.0 uses.** Amendments such as "plant CHK1 only" or "category X limit 1 lakh" then need no code.
- Operators: `eq, ne, in, not_in, lt, lte, gt, gte, between, exists`, plus `all/any/not` combinators.
- Validate with **JSON Schema / Pydantic**. A policy that fails validation can never be activated.
- Version selection: the version whose `effective_from` ≤ requirement date (record this choice in DECISIONS).

## Phase 3: Decision engine, a pure function (Wed morning, 6–8 h) ⚠ 40% of marks
`decide(batch, master_data, contracts, policy_versions, as_of) → decisions`. No DB or HTTP inside, so it is easy to test.
1. **Validate lines**, then mark NOT_PROCESSED (qty ≤ 0, unknown plant, etc.).
2. **Item match (1.1).** Code given, so trust it, but warn if the description disagrees. Otherwise use a normaliser (lowercase, `brg→ball bearing`, `hyd→hydraulic`, strip punctuation, unify tokens like `6205 2rs`/`6205-2RS`) with token / fuzzy score (`rapidfuzz`) above a threshold, plus a UoM sanity check. Below the threshold the line is `NEW`. Optional: an LLM tie-break that can only choose from the top-5 candidates or "none". Never invent items.
3. **Clubbing (2.2).** Sort by created_at, pr_no, line. Join the earliest open requirement with the same keys within the windows. New items are never clubbed.
4. **Contract eligibility (4.1–4.4):** item covered, date in range, supplier Active. Ceiling ledger in requirement-date order. Lowest rate with room.
5. **Valuation (3.1)** in the configured price order.
6. **Rules** evaluated in order. Record `clause`, `policy_version` and the contract/PO used.
7. **Preferred list (6.x)** as of the requirement date (Phase 4).
8. **Consolidation (7.1–7.2)** for PO outcomes only.
9. Emit rows in the exact output format. Format `reason` as: `v5.0 §5.2: RC-2026-013 rate 7.00, ceiling left 5,50,000`.

**Tests:** turn all 14 worked examples into pytest cases (supplier, price, value, route and PO grouping). Add unit tests for windows (boundary days), ceiling order and the blocked-supplier exclusion. Run them on every commit.

## Phase 4: Preferred supplier ranking (Wed, 2–3 h)
Eligibility per 6.1, Active only. Suggested explainable score (calibrate on the 3 example lists):
`score = 0.35·frequency_norm + 0.25·recency(1 / 0.5 / 0) + 0.15·contract_held + 0.15·on_time_pct + 0.10·quality/5`
- Store the weights in the policy JSON (`params.preferred_list.weights`) so they are versioned too.
- Tie-breaks: last PO date, then on-time %, then supplier_id (deterministic).
- Check against the examples: S029 > S030 for ITM-1048; S023 > S022 > S024 for ITM-1036; S009 > S010 for ITM-1016.
- Show buyers a score breakdown table for each supplier.
- Export `item_code, rank, supplier_id, reference_price` (top 3) as of a chosen date.
- 6.4 top-up: category suppliers ranked by on-time and quality.

## Phase 5: API (Wed afternoon, 2 h)
- `POST /api/process-batch` (multipart CSV, optional `policy_version`, returns JSON decisions) and `GET /api/batches/{id}/export.csv`
- `GET /api/preferred-suppliers/export.csv?as_of=YYYY-MM-DD`
- `POST /api/policy/propose` (file or text), `GET /api/policy/versions`, `POST /api/policy/{id}/approve`
- `POST /api/contracts/pdf` (extract, review, save), `POST /api/chat`, `POST /api/decisions/{id}/approve|override`
- Every mutation writes to `audit_log`.

## Phase 6: Policy update flow (Wed afternoon, 3 h) ⚠ the blind-test path
1. Upload an amendment PDF/DOCX/text, or describe the change in chat.
2. The LLM gets the **current policy JSON, the fact vocabulary and the schema**, and returns a **JSON patch plus an effective date**.
3. Validate the patch. Show a **plain-language diff** ("Spot PO limit: 2,00,000 → 3,00,000"; "New rule after 5.2: CHK1 Service items always RFQ"). Also show a **raw JSON editor** as a fallback, because you cannot change code on Thursday.
4. **Impact preview:** re-run the last batch under the draft and show "12 lines change route". This impresses reviewers and lets you sanity-check Run 2.
5. Approve. The draft becomes active from its effective date and the previous version is kept.
- **Rehearse:** write 4–5 fake amendments yourself (raise a limit, a plant-specific rule, a new category rule, change the clubbing window, block a supplier, a new route threshold) and confirm each works **without code changes**. This is the single most important rehearsal.

## Phase 7: Chat intake (Wed evening, 3–4 h)
- Slot filling: item, qty, uom, plant, need-by, urgency (plus an estimated price if it is a new item).
- The LLM extracts slots as JSON. The **matcher** proposes item candidates. The assistant asks only for missing slots, and offers the top-3 when matching is ambiguous. A clear "not in catalogue → new item, please give an estimated price" path.
- Resolve "next Friday" against today in IST. Confirm a summary card, then create the PR and show the instant decision preview.
- Batch CSV upload on the same screen.

## Phase 8: Buyer workspace + contracts UI (Wed night, 3 h)
- Requirements list (filter by route/status). Detail shows the PR lines, value, supplier/price or suggestions, the "why" panel (clause, version, contract/PO) and the preferred-list breakdown.
- **One primary button per screen**: Approve → creates PO / RFQ / RFP. Override requires a comment. Audit trail is visible.
- Contracts: register view, plus "Add from PDF" (extract, review form, save) with the ceiling starting at zero.
- Policy page: versions timeline, active badge, diff viewer.

## Phase 9: Docs + hardening (Thu 06:00–09:30)
- README: one-command run, env vars, hosted link, how to run Run 1 and Run 2.
- DECISIONS.md (see the list below), APPROACH.md (tools used, AI usage, architecture).
- Run the full sample batch end to end on the **hosted** app, compare with local, and check there are no blocked suppliers in the output.
- **Final commit before 09:45.** Tag it `v1-freeze`.

## Phase 10: Blind test day (Thu 10:00–12:00)
1. 10:00: upload the new batch under v5.0 and export `yourname__run1.csv`.
2. Upload the amendment through the app. Read the diff carefully and fix the parsed patch **in the JSON editor** if the LLM got it wrong (that is data, not code). Approve, reprocess, and export `yourname__run2.csv`.
3. Export the preferred list as of the given date as `yourname__preferred.csv`.
4. Sanity-check: row counts match the input, no blocked suppliers, and the amendment effect is visible.
5. Push only `submission/*.csv` by about 11:30. Do not touch code files.

---

## DECISIONS.md: issues already spotted in the data (start this file now)
| # | Observation | Suggested decision |
|---|---|---|
| 1 | PR-26-00172/1 has quantity **0** | NOT_PROCESSED: "invalid quantity" |
| 2 | PR-26-00173/1 plant **PUN2** (not in plants.csv) | NOT_PROCESSED: "unknown plant" |
| 3 | 3 pairs of **identical lines** (same plant/item/qty/need-by) | Decide: genuine duplicate submission or a real repeat need. Check requester and timestamps, then record the choice |
| 4 | RC-2026-005 supplier **S004 is Blocked** | Contract not applicable (4.1). Never a PO or suggestion (5.7) |
| 5 | RC-2025-020 **expired** 30 Sep 2026 | Not usable for POs, but still counts for preferred list 6.1(b) |
| 6 | RC-2026-031 / RC-2026-033 are **PDF-only** (start 15 Nov / 20 Nov) | Load through the PDF flow, consumed = 0. Apply only to requirements dated on or after the start |
| 7 | Batch rows are **not in creation order** (PR-00001 created 19 Nov) | Always sort by created_at, pr_no, line |
| 8 | Valuation 3.1(b) "most recent PO", even from a now-blocked supplier? | Decide, e.g. use any supplier's price for *valuation*, but Active only for *supplier choice* |
| 9 | Window boundaries ("within 14 days", "24 hours", "below" vs "≤") | Inclusive windows. Strict `<` for "below", `≤` for "at or below" |
| 10 | Which policy version applies, by requirement date or processing date? | Requirement date (2.3). Note what you do if an amendment's effective date is after a requirement date |
| 11 | New item with no estimated price | Value 0 / NOT_PROCESSED, or ask in chat. Pick one |
| 12 | Code given but description/UoM contradicts the item master | Trust the code, flag a warning in the reason |
| 13 | Ceiling consumption: only when a CONTRACT_PO is chosen, in requirement-date order | State it |
| 14 | Emergency but no history / over 5 lakh | Falls through to 5.4–5.6 |
| 15 | Capex item that also has a contract | 5.1 wins (rule order) |
| 16 | Preferred list has 0 suppliers for SPOT/EMERGENCY | Fall through to RFQ (record this) |
| 17 | Clubbed lines with mixed urgency or UoM | Any Emergency means emergency (2.3). Mixed UoM: do not club |
| 18 | Timezone | All timestamps IST |

## Risk list
- **Amendment adds a fact the engine does not expose.** Mitigation: a generous fact vocabulary, and a generic `pr.<field>` / `item.<field>` / `supplier.<field>` accessor.
- **LLM mis-parses the amendment on test day.** Mitigation: diff review plus the JSON editor, and rehearse.
- **Hosting cold starts or DB wipe on the free tier.** Mitigation: deploy early, seed master data on startup, keep a paid-tier fallback ready.
- **Running out of time.** Cut order: polish UI → chat niceties → PDF auto-extract (keep manual entry) → impact preview. **Never cut:** engine tests, policy JSON editor, CSV export.

## Timeline at a glance
| When | Phase |
|---|---|
| Tue day | 0, 1, 2 (deploy skeleton, data model, policy schema) |
| Wed 08–13 | 3 (engine + worked-example tests passing) |
| Wed 13–18 | 4, 5, 6 (ranking, API, policy update flow + rehearsal) |
| Wed 18–02 | 7, 8 (chat, buyer workspace, contracts UI) |
| Thu 06–09:45 | 9 (docs, hosted end-to-end check, freeze) |
| Thu 10–12 | 10 (blind test, push submission CSVs) |
