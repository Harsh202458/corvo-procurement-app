# APPROACH

## In one paragraph

A deterministic decision engine applies a policy that is stored as versioned JSON. AI is used at the edges, to read
free text (chat messages, amendment documents) into structure. Everything the AI produces is validated by the app and
shown to a person before it changes anything. The engine never calls the AI, so a batch always gets the same
decisions, and every decision carries the clause, the policy version and the contract or PO it relied on.

## Architecture

```
React UI (roles: requester / buyer / procurement)
        │  JSON over HTTP
FastAPI ── chat.py (slot filling)        ── Gemini (optional, extraction only)
        ├─ policy/  (schema, versions, change operations, diff, amendment readers) ── Gemini (optional)
        ├─ engine/  (pure: matching → clubbing → contracts → valuation → preferred list → rules → consolidation)
        └─ Postgres (SQLite locally): master data, contracts, policy versions, batches, runs, decisions, audit log
```

- **One deployable.** The UI is built into static files served by the API (Docker on Render, Postgres alongside).
- **Engine separated from everything else.** `backend/app/engine` takes plain dataclasses and returns results; it has no
  database, HTTP or AI code. The API stores each processing as a *run*, so Run 1 and Run 2 of the same batch sit side by side.
- **Data model.** Master data tables mirror the kit files. `policy_versions` holds full policy documents with status
  (draft, approved, rejected), effective date, the plain-language change list, source document text and who approved.
  `requirements` and `line_decisions` hold every decision with its facts and evidence. `overrides`, `procurement_docs`
  and `audit_log` cover buyer actions and history.

## Policy as data

A policy is one JSON document (`backend/app/policy/v5_0.json` is v5.0):

- **Parameters**: limits, clubbing and consolidation windows, lookbacks, valuation price order, ranking weights, supplier
  eligibility (statuses, minimum ratings, excluded suppliers).
- **Line checks**: conditions that make a line `NOT_PROCESSED` (quantity, plant, item, need-by date, duplicate).
- **Decision rules**, in order, first match wins: a condition over about 50 facts (value, item type, category, plant,
  departments, contract availability, purchase history, preferred-supplier ratings, any extra CSV column, …) and an
  outcome (PO or sourcing, route, supplier strategy, number of suggestions).

Conditions use a small language (`all` / `any` / `not`, 14 operators) and can refer to named parameters, so "raise the
spot limit" is a parameter change and "CHK1 services need quotes" is a new rule with a plant condition, placed before
the rule it overrides. A new kind of rule therefore needs no code. Every document is validated before it can be saved
(known facts and parameters, sensible outcomes, a PO must name its supplier strategy, …).

**Versioning.** Each requirement is decided under the approved version whose effective date is the latest on or before
the requirement date (D8). An amendment is a list of operations on the base version; the app computes a plain-language
diff itself and re-runs the latest batch with and without the change to show the impact line by line. A person
approves; nothing activates on its own.

## AI tools used

**Building the app: Claude Code (Anthropic, Claude Opus).** Used throughout for planning, writing code, tests and
documents, and for running the checks described below. Every design decision and every DECISIONS.md entry was reviewed;
I can explain and change any part.

**Inside the app: Google Gemini**, through a provider-agnostic client (`backend/app/llm.py`):

| Where | Model | What it does | What it does *not* do |
|---|---|---|---|
| Amendment reader | Gemini Flash, falling back to other Flash and Flash-Lite models | Turns amendment text into policy change operations, given the current policy, the fact vocabulary and the operation format; repairs its answer when validation fails | Decide anything: the app validates, diffs and previews; a person approves |
| Chat intake | Gemini Flash-Lite (8 s cap) | Extracts item text, quantity, plant, date and urgency from longer messages | Choose the item: matching is always the deterministic matcher, which never invents an item |

Without an API key, or when Gemini is unavailable, the app uses rule-based readers (regex extraction for chat; a
rule-based amendment reader for limits, windows, dates, versions and excluded suppliers). During the build Gemini was
overloaded for long stretches (503/504), which is why every call is capped and has fallbacks (D31, D37).

## How it was checked

- **80 automated tests** (`backend/tests`): all 14 worked examples (route, value, supplier, price, suggestions, clubbing,
  PO consolidation), the 3 example preferred-supplier rankings, no blocked or inactive supplier anywhere in the output,
  parsers, the condition language, policy validation, 10 rehearsal amendments that must need no code, the amendment
  readers (with a fake LLM, the repair loop and the fallback), chat conversations, and the full API flow including an
  amendment and a re-run.
- **Real-Gemini rehearsal** on four amendments written in the policy's style
  (`backend/tests/fixtures/amendments/`). It surfaced a rule-placement problem that the prompt now handles (D33).
- **Robustness:** a shuffled, reformatted copy of the sample batch (DD-MM-YYYY dates, `1,270` quantities, lowercase
  plants, other header names, an extra column) gives identical decisions, clubbing and PO grouping; PDF and DOCX
  amendments read correctly.
- **Hosted check:** the sample batch processed on Render matches the worked examples.

## Known limits

- No authentication: the acting user is chosen in the header (D7).
- Chat PRs are decided on their own, not clubbed with other open PRs (D36).
- The rule-based amendment reader covers common parameter changes only; new rules need the AI reader or the JSON editor.
- PO numbers in the output are this app's own; the worked examples' numbering may differ by the duplicate decision (D12).
