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
| D12 | Three pairs of lines look identical (same plant, item, quantity, need-by). Only one pair is from the **same requester** minutes apart: PR-26-00174/1 and PR-26-00175/1 (08:10 and 08:14). | A later line with the same requester, plant, item, quantity and need-by within 24 hours of a line still being processed is a **duplicate submission**: `NOT_PROCESSED`, citing the original (line check V5, so Procurement can change or remove it). Look-alikes from *different* requesters (laptops PR-26-00038/00044, SS sheet PR-26-00068/00154) are genuine separate needs and are clubbed per 2.2. | 2.1 forbids splitting a need, not raising it twice by accident; ordering the second servo drive would double-buy a 66k item. Different people asking for the same thing is exactly what clubbing is for. |
| D13 | 2.2 clubs "the same item" but lines can carry different units. | Lines are clubbed only when their UoM also matches (`clubbing.require_same_uom`). | Adding 10 KG to 10 NOS would make the requirement value meaningless. |
| D14 | Amendments can come as a document or a chat message, and an LLM may misread them. | Every change becomes a list of **operations** (set a parameter, add/update/move/remove a rule or check, set version/effective date). The result is validated (known facts, known parameters, sensible outcomes) and shown as a plain-language diff; it only takes effect when a person approves it. A JSON editor is the fallback. | The LLM can help read text but must never silently change how decisions are made. |
| D15 | Future amendments may use criteria v5.0 does not (plant, category, department, supplier rating, a new CSV column). | The engine exposes ~50 facts (see `/api/policy/vocabulary`), any extra CSV column as `pr.<column>`, named limits that rules reference by name, and supplier eligibility settings (status, minimum on-time %, minimum quality, excluded suppliers). New routes are free text. | So a new kind of rule needs no code change, which the blind test requires. |

## Decision engine

| # | Noticed | Decided | Why |
|---|---|---|---|
| D16 | Free-text descriptions use short forms ("brg", "hyd", "pneu", "welding rod", `monitor 24"`) and noise ("- urgent", "as per sample", "(repeat)"). | Deterministic matcher: expand known short forms, drop noise words, then accept a match only if **every number/spec the requester typed is in the item** (6308 never matches 6205; M10x50 never matches M10x40), the fuzzy score is ≥ 82 and it is clearly ahead of the runner-up. Otherwise the line is a new item. A given item code that exists is always used; an unknown code falls back to the description. | A wrong match orders the wrong part; a missed match only costs a sourcing round. In the sample all 37 coded-by-text lines match the right item and the 2 genuinely new items stay new. |
| D17 | Some descriptions say "urgent" while the urgency column says Normal (e.g. PR-26-00018, 00023, 00139, 00167). | Only the **urgency field** makes a line an emergency. | 2.3 says "marked Emergency"; free text is not a marking, and treating it as one would let anyone bypass quotes by typing a word. |
| D18 | 3.1(b) "the most recent PO for the item" does not say the supplier must be Active. | **Valuation** uses the most recent PO from any supplier (it is a price reference). **Eligibility** for spot/emergency POs (5.3, 5.4) needs a purchase from an Active supplier, as the rules say. | Keeps the value honest even when the last seller has since been blocked, without ever ordering from them (5.7). |
| D19 | Window boundaries. | 14 days: creation-date difference ≤ 14. Need-by: within ±7 days inclusive of the **first line's** need-by. 365 / 548-day lookbacks include the requirement date. Consolidation: ≤ 24 hours between the first lines' timestamps. | "Within" read as inclusive throughout; anchors are the first line, exactly as 2.2 and 7.1 say. |
| D20 | Contract ceiling bookkeeping. | Ceiling is consumed only when a requirement actually becomes a CONTRACT_PO, in requirement-date order, at the contract rate. A contract whose supplier is not Active does not apply (RC-2026-005, S004 Blocked). An expired contract (RC-2025-020) never applies for a PO, but still puts its supplier on the preferred list (6.1b). Ties on rate: lower contract id. | Follows 4.1 to 4.3; a capex item on contract still goes to capex sourcing (5.1 is first) and does not consume ceiling. |
| D21 | A spot/emergency rule can fit when no eligible supplier is left to receive the PO (e.g. after an amendment excludes one). | The rule is skipped and the next rule decides (usually RFQ); the reason records why. | 5.7 must never be broken, and sending to quotes is the safe fallback. |
| D22 | PR-26-00044 (laptops, 20 Nov) clubs into PR-26-00038's requirement (16 Nov). Laptop contract RC-2026-033 (PDF-only) starts 20 Nov. | The requirement date is 16 Nov (2.3), so the contract does not apply and the requirement (10 laptops, 7.1 lakh) goes to RFQ. | Literal 2.3. The buyer sees in the reason that the contract was not valid on the requirement date and can override with a comment if Procurement prefers to wait. |
| D23 | A new item may have no estimated price. | Still NEW_ITEM_SOURCING (the route does not depend on value), valued at 0 with a note. | Every new item goes to sourcing regardless (1.2); refusing the line would only delay it. |
| D24 | Output ids are "yours". | Per run: requirements `REQ-0001…` in creation order of their first line; POs `PO-0001…` in the order they are opened (7.2). | Stable and readable; re-running a batch gives the same ids. |
| D25 | PO unit price vs valuation price can differ (e.g. last PO from supplier A, preferred supplier B). | `unit_price` is the price the PO uses (contract rate, or the 1st preferred supplier's reference price, 6.3). `group_value_inr` is the requirement value from 3.1, which is what the limits were applied to. | Matches the worked examples (e.g. VCI bags: valued at 21.19, ordered from S027 at 21.59). |
| D26 | Some categories have fewer eligible suppliers than the 3 or 5 asked for (Fasteners has 2; S008 is Blocked). | Suggest all that exist and say so in the reason; never pad with ineligible suppliers. | Worked example 11 also lists only 4 for an RFP. |
| D27 | Policy versions may change mid-batch. | Each requirement uses the version in force on its requirement date; line checks use the version on the line's own date; clubbing windows come from the version on the requirement's first line. | One clock (D8) applied consistently. |


## Preferred supplier ranking (policy 6.2)

**Who is on the list (6.1):** every supplier that passes eligibility (Active, and any extra eligibility an amendment adds) and has at least one PO for the item in the 548 days up to the date, or a contract covering the item that started on or before the date (even if expired). Blocked or Inactive suppliers are listed as *excluded*, with the reason, so the buyer can see they were considered.

**Score.** Five signals, each scaled to 0..1, weighted and added:

| Signal | How it is measured | Weight | Why |
|---|---|---|---|
| Frequency | POs for the item in the 548 days ÷ the most POs any supplier on this list has | 0.30 | Repeated buying is the strongest sign the supplier works for this item. Relative to the list, so a niche item with 2 POs is not penalised against a fastener with 40. |
| Recency | Last PO for the item: ≤ 90 days → 1, ≤ 365 days → 0.5, older → 0 (the buckets the worked examples use) | 0.25 | Recent prices and capacity are more reliable; also links to 5.3/5.4, which need a purchase in the last 365 days. |
| Contract | 1 if the supplier has held a contract for the item (started on or before the date) | 0.20 | Procurement already vetted them for this item and negotiated a rate. |
| On-time | Supplier master on-time % ÷ 100 | 0.15 | Delivery record. |
| Quality | Supplier master quality rating ÷ 5 | 0.10 | Quality record. |

Ties: later last PO, then higher on-time %, then supplier id, so the result is always deterministic.

**Why this weighting.** History (frequency + recency, 55%) dominates because the list's job is "best choice for a *direct* PO" (6.2), where proven supply matters most. Contract comes next, then the delivery and quality records, which refine the order among suppliers with similar history. The ratings come from the supplier master, which is the official scorecard; the PO-history `delivered_on_time` flag is per order and noisier, so it is not used separately.

**Checks.**
- It reproduces all three example rankings exactly (ITM-1048: S029, S030, with S031 excluded as Inactive; ITM-1036: S023, S022, S024; ITM-1016: S009, S010). These are automated tests.
- Across the sample's 54 items with more than one eligible supplier, other reasonable weightings move the 1st preferred supplier for only a few items: equal weights 2, frequency-heavy 5, recency-heavy 3, quality-heavy 3, no contract signal 7. History-only moves it for 11, which is why the contract and rating signals are kept.

**Reference price (6.3):** that supplier's most recent PO price for the item in the 548 days; if none, the rate in its most recent contract for the item.

**Explainability:** the buyer sees each supplier's five signal values, the weights, the total and a one-line reason ("S029 score 0.933: 7 PO(s), last 2026-09-26; held a contract; on-time 81%, quality 3.1"). The weights, recency buckets and lookback days are policy parameters (`params.preferred_list`), so a change is versioned and approved like any other policy change.

**Export:** `GET /api/preferred-suppliers/export.csv?as_of=YYYY-MM-DD` writes `item_code, rank, supplier_id, reference_price` for the top 3 of every item that has a list, using the policy version in force on that date. Items with no eligible supplier have no rows.

**Sourcing suggestions (6.4):** the preferred list in order, then other eligible suppliers listing the item's category, best combined delivery and quality first (0.5 × on-time + 0.5 × quality, both scaled 0..1). New items get none.
