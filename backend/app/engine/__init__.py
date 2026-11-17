"""Decision engine: pure functions only (no DB, no HTTP, no LLM).

Input: PR lines, master data, contracts, policy version(s). Output: decisions.
Every limit, window and rule order comes from the policy JSON, never from code.
"""
