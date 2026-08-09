"""Independent corroboration — the anti-poisoning primitive, in one place.

Two pieces of evidence corroborate a fact only if they come from a different
session AND a different source-trust origin (`docs/trust.md` is the contract;
this module is that contract made real). The maximum-bipartite-matching
computation below (`max_independent_corroborations`) and the promotion rule
built on it (`determine_trust`) live here, not duplicated per caller, because
two callers need the exact same arithmetic for opposite reasons: the sleep
cycle (`mnemos_sleep_cycle.corroboration`) promotes a fact as new evidence
arrives, and the Warden (`mnemos_warden.revoke`) demotes one when evidence
turns out to be poisoned and must be set aside. A second copy that drifts
from this one is exactly how a memory-poisoning defense quietly stops meaning
what it claims to mean — see PHASE_06_GOVERNANCE_WARDEN.md's second-order
revocation test, which is the reason `independent_corroboration` accepts
`exclude_event_ids` at all.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

import psycopg

from .models import Trust

_TRUST_CATEGORIES = ("system", "operator", "agent", "external")


def max_independent_corroborations(signatures: set[tuple[UUID, str]]) -> int:
    """The size of the largest set of provenance signatures that are pairwise
    independent — every pair differing in BOTH session_id and source_trust.

    Counting *distinct* `(session_id, source_trust)` pairs is not the same
    thing and would overcount: two signatures from the same session but
    different source_trust values (a session with a mixed-trust episode list)
    share a session and so are not independent of each other under the
    "different session AND different source_trust" rule, even though they are
    literally two distinct pairs.

    This is exactly maximum bipartite matching — sessions on one side,
    source-trust categories on the other, an edge wherever a session
    contributed that category at least once — solved with a plain augmenting-
    path search. That is not overkill here: there are at most four categories,
    so the whole computation is O(sessions x 4).
    """
    sessions: dict[UUID, set[str]] = {}
    for session_id, trust in signatures:
        sessions.setdefault(session_id, set()).add(trust)

    match_to_session: dict[str, UUID] = {}

    def try_assign(session_id: UUID, seen: set[str]) -> bool:
        for trust in sessions[session_id]:
            if trust in seen:
                continue
            seen.add(trust)
            if trust not in match_to_session or try_assign(match_to_session[trust], seen):
                match_to_session[trust] = session_id
                return True
        return False

    matched = 0
    for session_id in sessions:
        if try_assign(session_id, set()):
            matched += 1
    return matched


def determine_trust(current: Trust, *, corroboration_count: int, has_trusted_source: bool) -> Trust:
    """The promotion rule, as a pure function so it can be tested without a
    database: system/operator provenance promotes directly; two independent
    sources promote to corroborated; anything else holds.

    CONTESTED and QUARANTINED never move via this function — both need an
    explicit resolution (a human, a supersession, or a fresh TTL window), not
    a corroboration count alone. A quarantined fact accumulating two more
    signatures from the SAME attacker-controlled pipeline should not walk
    itself back to legitimacy on volume — and, symmetrically, a fact
    revoke_source finds already quarantined for an unrelated reason should not
    be silently promoted back just because its remaining provenance still
    counts to two.
    """
    if current in (Trust.CONTESTED, Trust.QUARANTINED):
        return current
    if has_trusted_source:
        return Trust.TRUSTED
    if corroboration_count >= 2:
        return Trust.CORROBORATED
    return Trust.UNVERIFIED


async def independent_corroboration(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    fact_id: UUID,
    *,
    exclude_event_ids: Collection[UUID] = (),
) -> tuple[int, bool]:
    """Recount independent corroborating sources from the provenance graph.

    Returns `(corroboration_count, has_trusted_source)`. Does not write
    anything — callers combine this with `determine_trust` and are responsible
    for arming an audit ticket before whatever UPDATE follows, exactly like
    every other protected-table mutation in this codebase.

    `exclude_event_ids` answers a different question than the plain count:
    "what would this fact's corroboration look like without evidence from
    these episodes" — revoke_source's use, to test whether a fact's support
    survives once its tainted provenance is set aside, without writing
    anything or needing a second round-trip.
    """
    if exclude_event_ids:
        await cur.execute(
            """
            SELECT DISTINCT e.session_id, e.source_trust
            FROM mnemos.fact_provenance p
            JOIN mnemos.episodic_events e
              ON e.tenant_id = p.tenant_id AND e.event_id = p.event_id
            WHERE p.tenant_id = %s AND p.fact_id = %s AND NOT (p.event_id = ANY(%s))
            """,
            (tenant_id, fact_id, list(exclude_event_ids)),
        )
    else:
        await cur.execute(
            """
            SELECT DISTINCT e.session_id, e.source_trust
            FROM mnemos.fact_provenance p
            JOIN mnemos.episodic_events e
              ON e.tenant_id = p.tenant_id AND e.event_id = p.event_id
            WHERE p.tenant_id = %s AND p.fact_id = %s
            """,
            (tenant_id, fact_id),
        )
    signatures = {(row[0], str(row[1])) for row in await cur.fetchall()}
    count = max_independent_corroborations(signatures)
    has_trusted_source = any(trust in ("system", "operator") for _session, trust in signatures)
    return count, has_trusted_source


__all__ = [
    "determine_trust",
    "independent_corroboration",
    "max_independent_corroborations",
]
