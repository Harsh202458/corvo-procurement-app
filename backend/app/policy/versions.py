"""Choosing the policy version that applies to a requirement (pure)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .schema import PolicyDocument


@dataclass(frozen=True)
class ApprovedVersion:
    id: int
    document: PolicyDocument
    approved_seq: int  # later approval wins when two versions share an effective date

    @property
    def label(self) -> str:
        return self.document.version


def select_version(versions: list[ApprovedVersion], on: date) -> ApprovedVersion:
    """The approved version with the latest effective date on or before `on`.

    Ties on effective date go to the most recently approved. If `on` is before every
    version's effective date, the earliest version applies (see DECISIONS.md)."""
    if not versions:
        raise LookupError("no approved policy version")
    eligible = [v for v in versions if v.document.effective_from <= on]
    if not eligible:
        return min(versions, key=lambda v: (v.document.effective_from, v.approved_seq))
    return max(eligible, key=lambda v: (v.document.effective_from, v.approved_seq))
