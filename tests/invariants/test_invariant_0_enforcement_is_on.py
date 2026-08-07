"""INVARIANT 0 — the enforcement is actually switched on.

Runs before the other invariant suites and guards against the failure mode that
makes every one of them meaningless: a trigger that exists but is disabled.

This is not hypothetical. An early version of db/seed.py wrapped its teardown in
`ALTER TABLE ... DISABLE TRIGGER ALL / ENABLE TRIGGER ALL`. It crashed between
the two, leaving every invariant trigger disabled on the local database. The
invariant suites kept passing for a while — they assert that *bad* writes are
rejected, and the seeded data still satisfied them — until the checks that
depend on a live trigger started silently succeeding.

A security control that can be switched off by an unrelated crash needs a test
that asserts it is on. Everything downstream inherits its truth from this file.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.invariant

PROTECTED = [
    ("mnemos.episodic_events", "require_audit"),
    ("mnemos.semantic_facts", "require_audit"),
    ("mnemos.semantic_facts", "require_provenance"),
    ("mnemos.skill_versions", "require_audit"),
    ("mnemos.legal_holds", "require_audit"),
    ("mnemos.residency_policies", "require_audit"),
]


@pytest.mark.parametrize(("table", "trigger"), PROTECTED)
def test_invariant_trigger_exists_and_is_enabled(table: str, trigger: str, admin_conn) -> None:
    schema, name = table.split(".")
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT tgenabled
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND t.tgname = %s
            """,
            (schema, name, trigger),
        )
        row = cur.fetchone()

    assert row is not None, f"{trigger} is missing from {table} — invariant unenforced"
    # 'D' means disabled; anything else ('O', 'A', 'R') means it fires.
    assert row[0] != "D", (
        f"{trigger} on {table} EXISTS BUT IS DISABLED. "
        "Every other invariant test is meaningless until this is re-enabled: "
        f"ALTER TABLE {table} ENABLE TRIGGER ALL"
    )


def test_no_protected_table_has_all_triggers_disabled(admin_conn) -> None:
    """Catch the blunt instrument directly: DISABLE TRIGGER ALL on any table."""
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname || '.' || c.relname, t.tgname
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'mnemos' AND t.tgenabled = 'D'
            """
        )
        disabled = cur.fetchall()

    assert not disabled, f"disabled invariant triggers found: {disabled}"
