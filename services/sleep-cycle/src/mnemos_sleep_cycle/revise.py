"""Belief revision: what happens when a newly distilled claim meets what the
fabric already believes about the same subject.

Four outcomes, not two. Most memory systems either always insert (accumulating
duplicates and contradictions with no record of which won) or always overwrite
(silently destroying the evidence a deposition would need). Neither is
acceptable here:

  NOVEL      — nothing existing is close enough to compare against. Insert.
  REINFORCE  — a near-duplicate of an existing fact, and not contradictory.
               Strengthen the existing row; do not create a second one.
  SUPERSEDE  — contradicts an existing fact, and one side's evidence is
               clearly stronger. The loser is marked `superseded_by`, never
               deleted — supersession history is exactly what a deposition
               needs to explain a past decision.
  CONTEST    — contradicts an existing fact, and the evidence is comparable, or
               a contradiction judgment could not be made confidently. Both
               sides are marked `contested` and linked; `recall()` returns
               both rather than silently picking a winner.

The contradiction judgment is a cheap, narrowly-scoped model call whose output
is *structured data, not an action*: it can only steer which of the four
functions in `consolidate.py` gets called next. It never touches a row itself,
and an "unclear" verdict is deliberately treated the same as "contradictory" —
never silently folded into REINFORCE — because a corroboration mechanism that
can be talked into agreeing with itself by an uncertain judge is not a
corroboration mechanism.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from uuid import UUID

import psycopg
from mnemos_engine.crypto import DecryptionFailed, Envelope, row_aad
from mnemos_engine.embeddings import to_pgvector
from mnemos_engine.llm import ChatClient
from mnemos_engine.models import SourceTrust, Trust
from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger("mnemos.sleep_cycle.revise")

COMPARE_FLOOR = 0.75
"""Below this cosine similarity, two claims are not worth asking a model to
compare — they are about different things. Keeping this floor is what keeps
the contradiction judge to one call per distilled fact instead of one per
top-k candidate: everything below it is NOVEL without a model ever being
asked, which matters when the whole system runs on a five-dollar budget."""

REINFORCE_THRESHOLD = 0.92
"""At or above this similarity, a non-contradictory match is the same claim
restated, not a second claim. Below it but above COMPARE_FLOOR, a
non-contradictory match is treated as related-but-distinct (NOVEL) — "prefers
tea" and "prefers oolong tea" should not merge into one row."""

STRENGTH_MARGIN = 0.15
"""How close two contradictory claims' evidence scores must be before the
outcome is CONTEST instead of SUPERSEDE. Wide enough that a coin-flip
difference in confidence does not manufacture a false winner."""

_TRUST_STATE_BONUS: dict[Trust, float] = {
    Trust.TRUSTED: 0.4,
    Trust.CORROBORATED: 0.2,
    Trust.CONTESTED: 0.0,
    Trust.UNVERIFIED: 0.0,
    Trust.QUARANTINED: -0.2,
}

_CONTRADICTION_PROMPT = (
    resources.files("mnemos_sleep_cycle.prompts").joinpath("contradiction.md").read_text()
)


class Outcome(StrEnum):
    NOVEL = "novel"
    REINFORCE = "reinforce"
    SUPERSEDE = "supersede"
    CONTEST = "contest"


class Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class JudgedContradiction(Base):
    contradictory: bool | None
    reason: str = ""


@dataclass(frozen=True)
class ExistingMatch:
    fact_id: UUID
    fact_kind: str
    trust: Trust
    strength: float
    confidence: float
    corroboration_count: int
    text: str | None
    similarity: float


@dataclass(frozen=True)
class RevisionDecision:
    outcome: Outcome
    match: ExistingMatch | None = None
    reason: str | None = None
    new_wins: bool | None = None
    """Only meaningful when outcome is SUPERSEDE: True if the new candidate's
    evidence outweighs the existing fact's, False if the existing fact's
    evidence outweighs the new candidate's. The loser is the one that gets
    `superseded_by` set — which is not always the newly arriving claim."""


def evidence_strength(
    *, confidence: float, trust_bonus: float, corroboration_count: int, strength: float
) -> float:
    """A single comparable score for "how much should this claim be believed".

    Not a probability — a ranking heuristic with four inputs, each independently
    justified: the model's own calibrated confidence, a bonus for the trust
    state the claim already carries (a TRUSTED fact should not be casually
    outweighed by one new unverified claim), a bonus that grows with how many
    independent sources have already corroborated it, and a small bonus for
    reinforcement strength (log-scaled, same reasoning as the recall scorer in
    `mnemos_engine.engine` — repetition should have diminishing returns).
    """
    corroboration_bonus = min(0.3, 0.1 * corroboration_count)
    return confidence + trust_bonus + corroboration_bonus + 0.05 * math.log1p(strength)


def candidate_trust_bonus(source_trust: SourceTrust) -> float:
    """The trust-state bonus a brand-new candidate would carry, before it has
    a row: TRUSTED-on-arrival for system/operator provenance, baseline
    otherwise — mirroring `_TRUST_STATE_BONUS` for a fact that does not exist
    yet."""
    return _TRUST_STATE_BONUS[Trust.TRUSTED] if source_trust.is_trusted_on_arrival else 0.0


async def _nearest_facts(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    subject_key: str,
    vector: list[float],
    *,
    envelope: Envelope,
    k: int = 3,
) -> list[ExistingMatch]:
    """The candidates worth comparing a new claim against.

    Superseded and revoked facts are excluded — they are settled history, not
    a live claim a new one could reinforce, contest, or be measured against.
    Quarantined and contested facts ARE included: new evidence is exactly what
    might resolve either state.
    """
    literal = to_pgvector(vector)
    await cur.execute(
        """
        SELECT fact_id, fact_kind, trust, strength, confidence, corroboration_count,
               text_ciphertext, text_dek_wrapped, embedding <-> %s::VECTOR AS distance
        FROM mnemos.semantic_facts
        WHERE tenant_id = %s AND subject_key = %s AND superseded_by IS NULL
              AND revoked_at IS NULL AND embedding IS NOT NULL
        ORDER BY embedding <-> %s::VECTOR
        LIMIT %s
        """,
        (literal, tenant_id, subject_key, literal, k),
    )
    matches: list[ExistingMatch] = []
    for row in await cur.fetchall():
        distance = float(row[8])
        # cos_sim = 1 - d^2/2 holds exactly for unit vectors. OpenAI's
        # embeddings are unit-normalised by construction; FakeEmbedder
        # normalises explicitly (see embeddings.py) so tests exercise the same
        # formula real traffic does.
        cosine = 1.0 - (distance**2) / 2.0
        try:
            text: str | None = envelope.decrypt(
                bytes(row[6]), bytes(row[7]), aad=row_aad(tenant_id, subject_key)
            )
        except DecryptionFailed:
            text = None
        matches.append(
            ExistingMatch(
                fact_id=row[0],
                fact_kind=row[1],
                trust=Trust(row[2]),
                strength=float(row[3]),
                confidence=float(row[4]),
                corroboration_count=int(row[5]),
                text=text,
                similarity=cosine,
            )
        )
    return matches


async def _judge_contradiction(
    chat: ChatClient, *, existing: str, candidate: str
) -> JudgedContradiction:
    user = f"EXISTING CLAIM:\n{existing}\n\nNEW CLAIM:\n{candidate}"
    raw = await chat.complete_json(
        system=_CONTRADICTION_PROMPT, user=user, temperature=0.0, max_output_tokens=200
    )
    try:
        return JudgedContradiction.model_validate(raw)
    except (ValidationError, TypeError) as exc:
        log.warning(
            "contradiction judge returned an unparseable verdict (%s); treating as unclear", exc
        )
        return JudgedContradiction(contradictory=None, reason="judge response could not be parsed")


async def classify(
    cur: psycopg.AsyncCursor,
    chat: ChatClient,
    tenant_id: UUID,
    subject_key: str,
    *,
    candidate_text: str,
    candidate_vector: list[float],
    candidate_confidence: float,
    candidate_source_trust: SourceTrust,
    envelope: Envelope,
) -> RevisionDecision:
    """Decide what a newly distilled claim should do to existing memory.

    Exactly one model call at most (the contradiction judge), and only when a
    plausible near-duplicate exists — everything below `COMPARE_FLOOR` is
    NOVEL without ever asking.
    """
    matches = await _nearest_facts(cur, tenant_id, subject_key, candidate_vector, envelope=envelope)
    if not matches or matches[0].similarity < COMPARE_FLOOR:
        return RevisionDecision(Outcome.NOVEL)

    best = matches[0]
    judged = await _judge_contradiction(chat, existing=best.text or "", candidate=candidate_text)

    if judged.contradictory is False:
        if best.similarity >= REINFORCE_THRESHOLD:
            return RevisionDecision(Outcome.REINFORCE, match=best, reason=judged.reason)
        # Related but distinct — "prefers tea" does not reinforce "prefers
        # oolong tea", and it does not contradict it either. Insert fresh.
        return RevisionDecision(Outcome.NOVEL)

    # contradictory is True, or unclear (None) — never silently reinforce past
    # a genuine "no" from the judge.
    new_strength = evidence_strength(
        confidence=candidate_confidence,
        trust_bonus=candidate_trust_bonus(candidate_source_trust),
        corroboration_count=0,
        strength=1.0,
    )
    old_strength = evidence_strength(
        confidence=best.confidence,
        trust_bonus=_TRUST_STATE_BONUS[best.trust],
        corroboration_count=best.corroboration_count,
        strength=best.strength,
    )

    if abs(new_strength - old_strength) < STRENGTH_MARGIN:
        return RevisionDecision(Outcome.CONTEST, match=best, reason=judged.reason)
    return RevisionDecision(
        Outcome.SUPERSEDE, match=best, reason=judged.reason, new_wins=new_strength > old_strength
    )


__all__ = [
    "COMPARE_FLOOR",
    "REINFORCE_THRESHOLD",
    "STRENGTH_MARGIN",
    "ExistingMatch",
    "JudgedContradiction",
    "Outcome",
    "RevisionDecision",
    "candidate_trust_bonus",
    "classify",
    "evidence_strength",
]
