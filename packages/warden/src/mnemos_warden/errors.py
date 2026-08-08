"""Warden-specific errors. All subclass mnemos_engine's MnemosError so a caller
can catch broadly across both packages without knowing which one raised."""

from __future__ import annotations

from mnemos_engine.errors import MnemosError


class ConfirmationRequired(MnemosError):
    """A destructive operation was attempted without confirm=True.

    Not a formality. Every Warden operation requires an explicit confirmation,
    a stated reason, and — where dual control is on — a second admin key. This
    is the first of those gates, and it exists so a destructive call can never
    be a one-argument accident.
    """


class DualControlRequired(MnemosError):
    """The tenant requires two distinct admin approvals and only one is present."""

    def __init__(self, operation: str, first_approver: str) -> None:
        self.operation = operation
        self.first_approver = first_approver
        super().__init__(
            f"{operation} requires dual control: {first_approver} has approved, "
            "a second distinct admin approval is required"
        )


class UnknownSubject(MnemosError):
    """The named subject has no memory in this tenant.

    Distinguished from "erasure of nothing succeeded" — a caller who mistyped
    a subject key should see that plainly rather than a truthful but
    misleading zero-row proof.
    """
