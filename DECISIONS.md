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

_More decisions (clubbing, valuation, contracts, routing, ranking) are added as the engine is built._
