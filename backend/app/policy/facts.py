"""The vocabulary of facts a policy rule can test.

The engine computes all of these for every requirement (and the line-level ones for every
PR line). Policies may only reference facts listed here, plus free-form `pr.<column>` for
any extra column in the PR batch. Exposing more facts than v5.0 uses is deliberate: a future
amendment ("CHK1 only", "Safety & PPE items", "Maintenance department") needs no code.
"""
from __future__ import annotations

# name -> (type, description)
REQUIREMENT_FACTS: dict[str, tuple[str, str]] = {
    # item / requirement identity
    "item_code": ("string", "Resolved item code (NEW for new items)"),
    "item_description": ("string", "Item master description"),
    "item_type": ("string", "Item type from the item master: Material, Service or Capex"),
    "category": ("string", "Item category from the item master, e.g. 'Safety & PPE'"),
    "uom": ("string", "Unit of measure"),
    "plant": ("string", "Plant code, e.g. PUN1 or CHK1"),
    "is_new_item": ("bool", "The line matched no item in the item master (policy 1.2)"),
    # quantities, value, dates
    "value": ("number", "Requirement value in INR, excluding GST (policy 3.1)"),
    "unit_price": ("number", "Unit price used for valuation"),
    "price_source": ("string", "Where the valuation price came from: contract_rate, last_po_price, standard_price, estimated_unit_price"),
    "standard_price": ("number", "Item master standard price"),
    "total_quantity": ("number", "Total quantity of the requirement"),
    "line_count": ("number", "Number of PR lines clubbed into the requirement"),
    "requirement_date": ("date", "Creation date of the requirement's first line (policy 2.3)"),
    "earliest_need_by": ("date", "Earliest need-by date among the lines"),
    "days_to_need_by": ("number", "Days from the requirement date to the earliest need-by date"),
    # urgency / requesters
    "is_emergency": ("bool", "Any line in the requirement is marked Emergency (policy 2.3)"),
    "urgency": ("string", "Emergency if any line is Emergency, else Normal"),
    "departments": ("list", "Departments of the requesters on the lines"),
    "requesters": ("list", "Requester names on the lines"),
    # contracts
    "contract_available": ("bool", "A rate contract can be used: applies (4.1) and has ceiling left (4.2), best chosen per 4.3"),
    "contract_applies": ("bool", "At least one contract applies (4.1), whether or not it has ceiling left"),
    "contract_id": ("string", "The contract that will be used, if any"),
    "contract_rate": ("number", "Rate of the contract that will be used"),
    "contract_supplier_id": ("string", "Supplier of the contract that will be used"),
    "contract_ceiling_left": ("number", "Ceiling left on the contract that will be used, before this requirement"),
    # purchase history
    "bought_recently": ("bool", "The item was bought from an eligible (Active) supplier within the history lookback (default 365 days)"),
    "days_since_last_purchase": ("number", "Days since the most recent PO for the item, any supplier"),
    "last_po_price": ("number", "Price on the most recent PO for the item within the valuation lookback"),
    "po_count_lookback": ("number", "Number of POs for the item within the history lookback"),
    # preferred suppliers
    "preferred_count": ("number", "Number of eligible suppliers on the item's preferred list"),
    "preferred_supplier_id": ("string", "1st preferred supplier"),
    "preferred_supplier_on_time_pct": ("number", "On-time delivery % of the 1st preferred supplier"),
    "preferred_supplier_quality": ("number", "Quality rating (0-5) of the 1st preferred supplier"),
    "preferred_reference_price": ("number", "Reference price of the 1st preferred supplier (6.3)"),
    "category_supplier_count": ("number", "Eligible suppliers listing the item's category"),
}

LINE_FACTS: dict[str, tuple[str, str]] = {
    "pr_no": ("string", "PR number"),
    "plant": ("string", "Plant code on the line"),
    "plant_known": ("bool", "The plant exists in the plant master"),
    "quantity": ("number", "Quantity on the line (empty when unreadable)"),
    "uom": ("string", "Unit of measure on the line"),
    "uom_matches_item": ("bool", "The line's UoM equals the matched item's UoM (true for new items)"),
    "has_need_by": ("bool", "The line has a readable need-by date"),
    "need_by_before_created": ("bool", "The need-by date is before the creation date"),
    "has_item_reference": ("bool", "The line has an item code or a description"),
    "item_code_given": ("bool", "The requester gave an item code"),
    "item_code_known": ("bool", "The given item code exists in the item master (true if none given)"),
    "is_new_item": ("bool", "The line matched no item in the item master"),
    "item_type": ("string", "Item type of the matched item (empty for new items)"),
    "category": ("string", "Category of the matched item (empty for new items)"),
    "estimated_unit_price": ("number", "Requester's estimated unit price"),
    "urgency": ("string", "Normal or Emergency"),
    "department": ("string", "Requester's department"),
    "requester": ("string", "Requester"),
    "is_duplicate": ("bool", "Same requester, plant, item, quantity and need-by as an earlier line within the duplicate window"),
}

FACT_TYPES = {**{k: v[0] for k, v in LINE_FACTS.items()}, **{k: v[0] for k, v in REQUIREMENT_FACTS.items()}}


def is_known_fact(name: str, level: str) -> bool:
    if name.startswith("pr."):
        return True
    table = REQUIREMENT_FACTS if level == "requirement" else LINE_FACTS
    return name in table


def vocabulary_text() -> str:
    """For LLM prompts and the UI: the facts a rule may use."""
    out = ["Requirement-level facts (decision rules):"]
    out += [f"- {k} ({t}): {d}" for k, (t, d) in REQUIREMENT_FACTS.items()]
    out += ["", "Line-level facts (validation rules):"]
    out += [f"- {k} ({t}): {d}" for k, (t, d) in LINE_FACTS.items()]
    out += ["", "Any extra PR batch column is available as pr.<column_name> (string)."]
    return "\n".join(out)
