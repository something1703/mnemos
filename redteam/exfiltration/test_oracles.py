"""Attack class 2 — cross-tenant exfiltration by side channel (PHASE_10 10.2).

`tests/security/test_rls_isolation.py` already covers the direct routes: raw
SELECT, vector search, full-text search, CTE, subquery, JOIN, RETURNING, and
an unset or forged tenant context. Those all ask "can I read the row". This
module asks the quieter question — can tenant A learn that a row *exists* in
tenant B without ever reading it?

That matters for a vector-backed memory system specifically, and it is
under-discussed: similarity scores are a channel. If a query that happens to
match another tenant's secret scores differently from one that matches
nothing, the score alone leaks content, and no row was ever returned.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from mnemos_engine.db import Database
from mnemos_engine.engine import MnemosEngine
from mnemos_engine.models import SourceTrust

pytestmark = [pytest.mark.security, pytest.mark.redteam]

VICTIM_SECRET = "The acquisition of Northwind Trading closes on the fourteenth of March."


async def _make_tenant(db: Database, label: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()

    async def create(cur: psycopg.AsyncCursor) -> None:
        await cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name, default_region) "
            "VALUES (%s, %s, %s, %s)",
            (tenant_id, f"redteam-{label}-{tenant_id.hex[:8]}", "Red team", "us-east-1"),
        )

    await db.transaction(None, create, label="create_tenant")
    return tenant_id


async def test_similarity_scores_do_not_reveal_another_tenants_content(
    db: Database, engine: MnemosEngine
) -> None:
    """The score oracle.

    The victim stores a secret. The attacker, in a different tenant, recalls
    the secret's exact text — the strongest possible probe, since an unscoped
    ANN index would rank it first — and then recalls unrelated nonsense. If
    the index leaked across tenants, the first probe would score higher than
    the second. Both must come back with nothing at all: not a low score, no
    result, because the index is prefix-scoped by tenant_id and the victim's
    neighbourhood is not reachable to search in the first place.
    """
    victim = await _make_tenant(db, "victim")
    attacker = await _make_tenant(db, "attacker")

    await engine.remember(
        victim,
        subject_key="deal:northwind",
        session_id=uuid.uuid4(),
        event_type="note",
        content=VICTIM_SECRET,
        source_trust=SourceTrust.OPERATOR,
    )

    exact = await engine.recall(attacker, VICTIM_SECRET, include_unverified=True, reinforce=False)
    nonsense = await engine.recall(
        attacker, "unrelated gibberish zzzz", include_unverified=True, reinforce=False
    )

    assert exact.facts == [], "an exact-text probe from another tenant returned something"
    assert nonsense.facts == []
    # Equality of the *observable* result is the actual anti-oracle property:
    # the attacker cannot distinguish "there is a matching secret next door"
    # from "there is nothing anywhere".
    assert len(exact.facts) == len(nonsense.facts)
    assert exact.unverified_withheld == nonsense.unverified_withheld, (
        "withheld counts must not differ either"
    )


async def test_recall_withheld_count_does_not_count_other_tenants_rows(
    db: Database, engine: MnemosEngine
) -> None:
    """`recall` discloses how many facts it filtered out, which is a good
    honesty property (`docs/trust.md`) and exactly the kind of counter that
    leaks if it is computed before tenant scoping.

    The victim gets several `unverified` facts — the class recall withholds.
    The attacker's withheld count must stay zero regardless.
    """
    victim = await _make_tenant(db, "victim")
    attacker = await _make_tenant(db, "attacker")
    session = uuid.uuid4()

    for i in range(3):
        await engine.remember(
            victim,
            subject_key="deal:northwind",
            session_id=session,
            event_type="note",
            content=f"{VICTIM_SECRET} Detail {i}.",
            source_trust=SourceTrust.AGENT,
        )

    result = await engine.recall(attacker, VICTIM_SECRET, reinforce=False)
    assert result.facts == []
    assert result.unverified_withheld == 0, "the withheld counter leaked another tenant's row count"


async def test_reading_another_tenants_fact_id_is_indistinguishable_from_a_missing_one(
    db: Database, engine: MnemosEngine
) -> None:
    """The error-message oracle.

    An attacker who holds a real fact_id from tenant B (leaked in a log, a
    screenshot, a support ticket) must not be able to confirm it is real by
    the *shape* of the failure. "Not found" and "not yours" have to be the
    same answer, or the error message is a membership test.
    """
    victim = await _make_tenant(db, "victim")
    attacker = await _make_tenant(db, "attacker")

    await engine.remember(
        victim,
        subject_key="deal:northwind",
        session_id=uuid.uuid4(),
        event_type="note",
        content=VICTIM_SECRET,
        source_trust=SourceTrust.OPERATOR,
    )

    async def real_fact_id(cur: psycopg.AsyncCursor) -> uuid.UUID | None:
        await cur.execute(
            "SELECT fact_id FROM mnemos.semantic_facts WHERE tenant_id = %s LIMIT 1", (victim,)
        )
        row = await cur.fetchone()
        return row[0] if row else None

    existing = await db.transaction(victim, real_fact_id, label="peek", read_only=True)

    async def lookup(cur: psycopg.AsyncCursor, fact_id: uuid.UUID) -> int:
        await cur.execute(
            "SELECT count(*) FROM mnemos.semantic_facts WHERE tenant_id = %s AND fact_id = %s",
            (attacker, fact_id),
        )
        return (await cur.fetchone())[0]

    invented = uuid.uuid4()
    if existing is not None:
        seen_real = await db.transaction(
            attacker, lambda cur: lookup(cur, existing), label="probe_real", read_only=True
        )
        assert seen_real == 0

    seen_fake = await db.transaction(
        attacker, lambda cur: lookup(cur, invented), label="probe_fake", read_only=True
    )
    assert seen_fake == 0, "a real id and an invented one must be equally invisible"
