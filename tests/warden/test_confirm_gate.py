"""Every Warden operation requires confirm=True and a non-empty reason.
Neither is a formality (see warden.py's module docstring), so both halves of
the gate are tested for every destructive method — not just one representative
case — because a gate that protects `forget` but not `shred` is not a gate.
"""

from __future__ import annotations

import uuid

import pytest
from mnemos_warden.errors import ConfirmationRequired

pytestmark = pytest.mark.invariant

DESTRUCTIVE_CALLS = [
    "redact",
    "forget",
    "quarantine",
    "shred",
]


@pytest.mark.parametrize("method_name", DESTRUCTIVE_CALLS)
async def test_confirm_false_is_refused(warden, tenant: uuid.UUID, method_name: str) -> None:
    method = getattr(warden, method_name)
    with pytest.raises(ConfirmationRequired, match="confirm=True"):
        await method(tenant, "patient:gate", actor="test", reason="a reason", confirm=False)


@pytest.mark.parametrize("method_name", DESTRUCTIVE_CALLS)
async def test_confirm_omitted_defaults_to_refused(
    warden, tenant: uuid.UUID, method_name: str
) -> None:
    """The default must be the safe one. A destructive method callable
    without ANY confirm argument would be a one-argument accident waiting to
    happen — exactly what this gate exists to prevent."""
    method = getattr(warden, method_name)
    with pytest.raises(ConfirmationRequired):
        await method(tenant, "patient:gate", actor="test", reason="a reason")


@pytest.mark.parametrize("method_name", DESTRUCTIVE_CALLS)
async def test_empty_reason_is_refused_even_with_confirm(
    warden, tenant: uuid.UUID, method_name: str
) -> None:
    method = getattr(warden, method_name)
    with pytest.raises(ConfirmationRequired, match="reason"):
        await method(tenant, "patient:gate", actor="test", reason="", confirm=True)


@pytest.mark.parametrize("method_name", DESTRUCTIVE_CALLS)
async def test_whitespace_only_reason_is_refused(
    warden, tenant: uuid.UUID, method_name: str
) -> None:
    """Not just falsy — a reason of all whitespace is functionally empty and
    must be rejected the same way, or the check is trivially bypassable."""
    method = getattr(warden, method_name)
    with pytest.raises(ConfirmationRequired, match="reason"):
        await method(tenant, "patient:gate", actor="test", reason="   \n\t  ", confirm=True)


async def test_revoke_source_requires_confirm(warden, tenant: uuid.UUID) -> None:
    with pytest.raises(ConfirmationRequired):
        await warden.revoke_source(
            tenant, [uuid.uuid4()], actor="test", reason="a reason", confirm=False
        )


async def test_revoke_source_requires_a_reason(warden, tenant: uuid.UUID) -> None:
    with pytest.raises(ConfirmationRequired):
        await warden.revoke_source(tenant, [uuid.uuid4()], actor="test", reason="", confirm=True)


async def test_set_legal_hold_requires_confirm(warden, tenant: uuid.UUID) -> None:
    with pytest.raises(ConfirmationRequired):
        await warden.set_legal_hold(
            tenant,
            "patient:gate",
            matter_reference="M-1",
            placed_by="c",
            confirm=False,
        )


async def test_release_legal_hold_requires_confirm(warden, tenant: uuid.UUID) -> None:
    """Even a release — which relaxes rather than tightens control — goes
    through the same gate, because 'this reduces restriction' is not the same
    claim as 'this is safe to trigger accidentally'."""
    with pytest.raises(ConfirmationRequired):
        await warden.release_legal_hold(
            tenant, uuid.uuid4(), released_by="c", release_reason="done", confirm=False
        )


async def test_reads_need_no_confirmation(warden, tenant: uuid.UUID) -> None:
    """The gate protects mutation, not visibility. A preview, a hold check, a
    residency report — none of these destroy anything, and none should
    require the ceremony that destruction requires."""
    from mnemos_warden.errors import UnknownSubject
    from mnemos_warden.models import EraseMode

    with pytest.raises(UnknownSubject):
        # Reaches past the confirm gate entirely and fails on "no such
        # subject" instead — proving no confirm/reason check intercepted it.
        await warden.preview_erasure(tenant, "patient:never-existed", EraseMode.FORGET)

    assert await warden.check_hold(tenant, "patient:gate") is None
    report = await warden.where_is(tenant, "patient:gate")
    assert report.episode_regions == {}
