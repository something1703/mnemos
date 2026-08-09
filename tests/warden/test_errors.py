"""The Warden's error types, in isolation — carried fields matter as much as
the type: a compliance officer reading a caught exception needs the operator
name and the operation, not just "dual control required"."""

from __future__ import annotations

from mnemos_warden.errors import DualControlRequired


def test_dual_control_required_carries_operation_and_first_approver() -> None:
    error = DualControlRequired("forget", "compliance-officer-1")

    assert error.operation == "forget"
    assert error.first_approver == "compliance-officer-1"
    assert "forget" in str(error)
    assert "compliance-officer-1" in str(error)
    assert "second distinct admin approval" in str(error)
