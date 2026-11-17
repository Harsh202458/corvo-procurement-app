from fastapi import Header


def current_user(x_user: str | None = Header(default=None)) -> str:
    """Who is acting. The UI sends X-User from its role switcher (no auth in this prototype)."""
    return (x_user or "anonymous").strip()[:100] or "anonymous"
