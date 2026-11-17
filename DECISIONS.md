# DECISIONS

Where the policy (v5.0), the brief or the data is silent or ambiguous: what I noticed, what I decided, and why.

## Data intake

| # | Noticed | Decided | Why |
|---|---|---|---|
| D1 | Timestamps have no time zone. | All `created_at` values and windows are treated as **IST** (naive local time). | Corvo's plants are in Pune; mixing zones would shift 24-hour and 14-day windows. |
| D2 | A batch file could contain the same `pr_no`/`pr_line` twice. | Keep the **first** occurrence, report the rest as an upload issue. | The pair is the line's identity in the output format; two rows with one key cannot both be scored. |
| D3 | Rows can have unreadable values (quantity, need-by date). | **Keep** the row with the value empty, so the engine marks it `NOT_PROCESSED` with a reason. Only rows with no readable `created_at` are dropped (reported). | Silently dropping a line hides it from the buyer; every input line should get an output row. |
| D4 | Blind-test files may differ slightly in header spelling or number format. | Headers match case-insensitively with common synonyms; numbers accept `2,00,000` and `₹`; dates accept ISO and DD-MM-YYYY. Extra columns are kept on the line (available to policy rules). | Robustness without code changes after the freeze. |
| D5 | The contract register is a monthly snapshot; PDF-only contracts are added by buyers. | Re-uploading the register replaces register contracts but **keeps PDF/manual contracts**, unless the register now has the same contract id (then the register wins). | Avoids losing buyer-entered contracts on the next monthly upload. |
| D6 | Supplier categories are `;`-separated (e.g. `Electrical Spares;Capital Equipment`). | Split on `;` (or `|`). | Needed for 6.4 category top-ups. |
| D7 | No login is required by the brief. | The acting user is taken from an `X-User` header set by the UI's role switcher, and written to the audit log. | Keeps "who and when" on every action without building auth. |

## Policy as data

| # | Noticed | Decided | Why |
|---|---|---|---|
| D8 | The brief says a new version activates "from its effective date", but not which date a decision is compared with. | A requirement is decided under the approved version whose effective date is the latest **on or before the requirement date** (policy 2.3). Two versions with the same effective date: the one approved later wins. A requirement dated before every version uses the earliest. | 2.3 already makes the requirement date the reference for contracts and history; using it for the policy too keeps one consistent clock, and re-running an old batch reproduces old decisions. |
| D9 | Policy 1.2 (new items) and 5.1 (capex) both send to sourcing; the order is not stated between section 1 and section 5. | New-item rule is evaluated first. | A new item has no item type, so 5.1 cannot be tested; 1.2 says it "always" goes to NEW_ITEM_SOURCING. |
| D10 | The policy does not say what to do with unusable lines. | Line checks run before any rule and are **policy data** too (V1 quantity not positive, V2 unknown plant, V3 no item, V4 no readable need-by): such lines are `NOT_PROCESSED` with the reason. | A blank or zero quantity cannot be valued; an unknown plant (e.g. PUN2) is outside the policy's scope; without a need-by date 2.2 cannot be applied. Procurement can change these checks like any rule. |
| D11 | Limits use different words: "below 2,00,000", "≤ 5,00,000", "10,00,000 or more". | Spot: value **<** 2,00,000. Emergency: **≤** 5,00,000. RFQ: **<** 10,00,000; RFP: **≥** 10,00,000. Each comparison is stored with its operator, so an amendment can change it. | Follows the policy wording exactly; worked example 8 confirms ≤ for emergency. |
| D12 | Three pairs of lines look identical (same plant, item, quantity, need-by), e.g. PR-26-00174/00175 four minutes apart. | **Not** removed as duplicates. They are clubbed per 2.2 and the reason notes a possible duplicate (`is_duplicate` fact). Procurement can add a line check on `is_duplicate` if it wants them rejected. | 2.1 tells Procurement to treat similar lines as one requirement, not to discard any; the pairs differ in requester, department or estimate, so they may be genuine. Dropping a real need is worse than a buyer seeing a flag. |
| D13 | 2.2 clubs "the same item" but lines can carry different units. | Lines are clubbed only when their UoM also matches (`clubbing.require_same_uom`). | Adding 10 KG to 10 NOS would make the requirement value meaningless. |
| D14 | Amendments can come as a document or a chat message, and an LLM may misread them. | Every change becomes a list of **operations** (set a parameter, add/update/move/remove a rule or check, set version/effective date). The result is validated (known facts, known parameters, sensible outcomes) and shown as a plain-language diff; it only takes effect when a person approves it. A JSON editor is the fallback. | The LLM can help read text but must never silently change how decisions are made. |
| D15 | Future amendments may use criteria v5.0 does not (plant, category, department, supplier rating, a new CSV column). | The engine exposes ~50 facts (see `/api/policy/vocabulary`), any extra CSV column as `pr.<column>`, named limits that rules reference by name, and supplier eligibility settings (status, minimum on-time %, minimum quality, excluded suppliers). New routes are free text. | So a new kind of rule needs no code change, which the blind test requires. |

_More decisions (clubbing, valuation, contracts, routing, ranking) are added as the engine is built._
