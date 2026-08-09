"""ADR-011's allowlist checked against two things it must never drift from:
the vendored skill set (a skill with no allowlist entry is allowed nothing,
silently — this catches that at test time instead) and the read-only tool
catalog (an allowlist entry naming a write-capable tool would be the exact
gap the read-only guarantee exists to prevent)."""

from __future__ import annotations

from mnemos_custodian.allowlist import (
    ALLOWLIST,
    EXCLUDED_FREE_TEXT_TOOLS,
    READ_ONLY_TOOLS,
    WRITE_CAPABLE_TOOLS,
    is_allowed,
    tools_for,
)
from mnemos_custodian.skills import load_all


def test_every_vendored_skill_has_an_allowlist_entry() -> None:
    skill_ids = set(load_all())
    assert skill_ids <= set(ALLOWLIST), (
        f"skills with no allowlist entry: {skill_ids - set(ALLOWLIST)}"
    )


def test_every_allowlist_entry_has_a_vendored_skill() -> None:
    """The reverse direction: an allowlist entry for a skill that was never
    vendored is dead configuration, and a sign the allowlist drifted from
    the skill set rather than the other way around."""
    skill_ids = set(load_all())
    assert set(ALLOWLIST) <= skill_ids


def test_no_allowlist_entry_names_a_write_capable_tool() -> None:
    for entry in ALLOWLIST.values():
        assert entry.mcp_tools.isdisjoint(WRITE_CAPABLE_TOOLS), (
            f"{entry.skill_id} allowlists a write-capable tool: "
            f"{entry.mcp_tools & WRITE_CAPABLE_TOOLS}"
        )


def test_no_allowlist_entry_names_an_excluded_free_text_tool() -> None:
    for entry in ALLOWLIST.values():
        assert entry.mcp_tools.isdisjoint(EXCLUDED_FREE_TEXT_TOOLS), (
            f"{entry.skill_id} allowlists select_query, deliberately excluded"
        )


def test_every_allowlisted_tool_is_a_known_read_only_tool() -> None:
    for entry in ALLOWLIST.values():
        assert entry.mcp_tools <= READ_ONLY_TOOLS, (
            f"{entry.skill_id} allowlists unknown tool(s): {entry.mcp_tools - READ_ONLY_TOOLS}"
        )


def test_no_allowlist_entry_is_empty() -> None:
    for entry in ALLOWLIST.values():
        assert entry.mcp_tools, f"{entry.skill_id} allowlists no tools at all"


def test_is_allowed_true_for_a_mapped_tool() -> None:
    assert is_allowed("triaging-live-sql-activity", "show_running_queries") is True


def test_is_allowed_false_for_an_unmapped_tool() -> None:
    assert is_allowed("triaging-live-sql-activity", "create_table") is False


def test_is_allowed_false_for_an_unknown_skill() -> None:
    """An unmapped skill_id is allowed nothing — not everything, which is
    what a naive `.get(skill_id, READ_ONLY_TOOLS)`-shaped bug would do."""
    assert is_allowed("some-skill-nobody-registered", "list_tables") is False


def test_tools_for_unknown_skill_raises() -> None:
    import pytest

    with pytest.raises(KeyError, match="no allowlist entry"):
        tools_for("some-skill-nobody-registered")
