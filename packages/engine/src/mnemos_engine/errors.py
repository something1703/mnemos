"""Typed errors.

Every failure an agent can provoke has a distinct type carrying the fields a
caller needs to act. A memory system that raises bare `Exception` forces callers
to parse English, and an agent parsing English is an agent that will eventually
retry a legal-hold refusal as if it were a transient error.
"""

from __future__ import annotations

from datetime import datetime


class MnemosError(Exception):
    """Base for everything raised by the engine and the Warden."""


class InvariantViolation(MnemosError):
    """A sacred invariant was about to be broken. Never caught and continued."""


class TenantContextMissing(MnemosError):
    """An operation was attempted without a tenant bound to the session.

    Fails closed: RLS would return zero rows, which reads as "no data" rather
    than "you forgot something". Raising here makes the bug obvious at the call
    site instead of silently producing empty recalls.
    """


class OutsideTemporalWindow(MnemosError):
    """recall_as_of() asked for an instant older than the MVCC GC window.

    Carries the real boundary so the caller can decide, rather than silently
    degrading to `now()` — which would answer a question about the past with
    facts from the present, the single most dangerous failure this API has.
    """

    def __init__(self, requested: datetime, earliest: datetime, gc_ttl_seconds: int) -> None:
        self.requested = requested
        self.earliest = earliest
        self.gc_ttl_seconds = gc_ttl_seconds
        super().__init__(
            f"requested {requested.isoformat()} but history only reaches back to "
            f"{earliest.isoformat()} (gc.ttlseconds={gc_ttl_seconds}). "
            "Subjects under legal hold get an extended window; see Phase 06.3."
        )


class ResidencyViolation(MnemosError):
    """A write or read would move data across a jurisdiction it may not cross."""

    def __init__(self, subject_key: str, home_region: str, attempted_region: str) -> None:
        self.subject_key = subject_key
        self.home_region = home_region
        self.attempted_region = attempted_region
        super().__init__(
            f"{subject_key} is homed in {home_region} and may not be written from "
            f"{attempted_region}"
        )


class LegalHoldActive(MnemosError):
    """Erasure refused because the subject is under hold.

    Carries the matter reference deliberately: a refusal a compliance officer
    cannot act on is barely better than a silent failure.
    """

    def __init__(self, subject_key: str, matter_reference: str, placed_by: str) -> None:
        self.subject_key = subject_key
        self.matter_reference = matter_reference
        self.placed_by = placed_by
        super().__init__(
            f"cannot erase {subject_key}: legal hold {matter_reference} placed by {placed_by}"
        )


class ChainBroken(MnemosError):
    """Ledger verification found a link whose hash does not recompute."""

    def __init__(self, shard_id: int, seq: int, expected: str, found: str) -> None:
        self.shard_id = shard_id
        self.seq = seq
        self.expected = expected
        self.found = found
        super().__init__(
            f"chain broken at shard {shard_id} seq {seq}: expected {expected}, found {found}"
        )


class NotCanonical(MnemosError):
    """A payload could not be canonically serialized for hashing.

    Raised rather than coerced. A hash computed over a value we silently
    reinterpreted is a hash nobody else can reproduce, which defeats the point.
    """
