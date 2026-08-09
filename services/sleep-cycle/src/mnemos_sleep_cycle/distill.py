"""Turn a session's episodes into candidate facts.

This is the one place in the pipeline that reads free text and decides what it
means, which makes it the ceiling on both extraction quality and injection
resistance. Two defenses, deliberately layered rather than relying on either
alone:

1. **In the prompt** (`prompts/distill.md`): episode content is wrapped in an
   explicit delimiter and the model is told content is data, never
   instruction.
2. **In this code**: nothing this module returns is trusted. A `DistilledFact`
   is a candidate — it lands at `trust='unverified'` regardless of how
   confident the model claims to be, and it does not become recallable until
   the corroboration gate (Phase 05.4) says so. If the prompt defense fails
   completely and a poisoned episode produces a plausible-looking fact, that
   fact still cannot self-promote. Assume layer 1 fails; layer 2 is what
   actually holds, and Phase 10 attacks both.

The `subject_key` a fact is written under is never taken from the model. It is
inherited from the episodes that produced it, which is what keeps residency
correct without needing to trust an LLM to reason about jurisdiction — see
`consolidate.py` for why batches are grouped by subject in the first place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from uuid import UUID

from mnemos_engine.llm import ChatClient, LLMError
from mnemos_engine.models import SourceTrust
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

log = logging.getLogger("mnemos.sleep_cycle.distill")

MAX_FACTS_PER_SESSION = 8
DELIMITER_OPEN = "<<<EPISODE_CONTENT>>>"
DELIMITER_CLOSE = "<<<END_EPISODE_CONTENT>>>"

_PROMPT = resources.files("mnemos_sleep_cycle.prompts").joinpath("distill.md").read_text()


class Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


@dataclass(frozen=True)
class EpisodeInput:
    """One decrypted episode, ready to be shown to the model.

    `index` is the 1-based position in this batch, and is what
    `source_indices` refers back to — not the event_id, which is longer, more
    typo-prone for a model to reproduce exactly, and gains nothing here since
    the mapping back to a real event_id happens in code regardless.
    """

    index: int
    event_id: UUID
    occurred_at: datetime
    content: str
    source_trust: SourceTrust
    """Never shown to the model — the prompt only sees content and a date.
    Carried alongside so `consolidate.py` can determine a distilled fact's
    provenance trust (system/operator origin promotes directly; see
    `corroboration.py`) without a second query back to episodic_events."""

    event_type: str = "note"
    """Also never shown to the model, and used for exactly one decision:
    `ops_finding` episodes bypass distillation entirely (see
    `_passthrough_fact`)."""


class DistilledFact(Base):
    fact_text: str
    fact_kind: str
    confidence: float
    source_indices: list[int]
    contradicts_hint: str | None = None

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        # Clamped, not rejected: an otherwise-good fact should not be thrown
        # away because the model ignored "do not use 1.0". The prompt sets
        # calibration expectations; this is the backstop, not the primary
        # control — a model that is systematically overconfident is a prompt
        # problem the golden eval (Phase 05.1) is what catches.
        return max(0.0, min(1.0, value))

    @field_validator("fact_text")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("fact_text must not be empty")
        return stripped


class DistillationResult(Base):
    facts: list[DistilledFact]
    episodes_considered: int
    dropped_no_source: int = 0
    dropped_invalid: int = 0


def _render_user_prompt(episodes: list[EpisodeInput]) -> str:
    blocks = [
        f"[{ep.index}] ({ep.occurred_at.date().isoformat()})\n"
        f"{DELIMITER_OPEN}\n{ep.content}\n{DELIMITER_CLOSE}"
        for ep in episodes
    ]
    return "\n\n".join(blocks)


PASSTHROUGH_EVENT_TYPE = "ops_finding"
"""Episodes of this type skip the model and become facts verbatim.

Measured against the live cluster before being written: four Custodian sweeps
produced near-identical findings ("Cluster is not in the RUNNING state", three
times word for word), and distillation rewrote each into a different long
sentence. The resulting facts sat at 0.67-0.84 pairwise cosine similarity —
under `revise.REINFORCE_THRESHOLD` (0.92), and some under `COMPARE_FLOOR`
(0.75), so four agreeing observations became four separate `unverified` facts
that could never corroborate each other.

Distillation exists to turn a messy conversation into a crisp claim. An
`ops_finding` arrives *as* a crisp claim — one structured-output `summary`
field with its own severity and evidence — so re-describing it adds nothing
and destroys the only property corroboration depends on: that the same
observation, observed twice, produces the same sentence twice.

Deliberately a passthrough and not a lower similarity threshold. Loosening
`REINFORCE_THRESHOLD` to 0.67 would let genuinely distinct claims merge across
every tenant and vertical; fixing the drift at its source costs nothing
elsewhere."""

_PASSTHROUGH_CONFIDENCE = 0.6
"""Enough to be a real claim, deliberately short of the model-authored range.
The finding is a faithful restatement of what a tool returned, so it does not
get to arrive pre-confident — it still has to earn promotion through the
corroboration gate like anything else."""


def _passthrough_fact(episode: EpisodeInput) -> DistilledFact:
    """One `ops_finding` episode, restated as itself."""
    return DistilledFact(
        fact_text=episode.content,
        fact_kind=PASSTHROUGH_EVENT_TYPE,
        confidence=_PASSTHROUGH_CONFIDENCE,
        source_indices=[episode.index],
    )


async def distill_session(
    chat: ChatClient,
    episodes: list[EpisodeInput],
    *,
    max_facts: int = MAX_FACTS_PER_SESSION,
) -> DistillationResult:
    """Extract candidate facts from one subject's episodes in one session.

    Raises `mnemos_engine.llm.LLMError` if the model could not be reached or
    never returned parseable JSON even after one repair attempt — the caller
    (`consolidate.py`) decides what that means for the batch (skip and retry
    next run; the episodes stay unconsolidated either way).
    """
    if not episodes:
        return DistillationResult(facts=[], episodes_considered=0)

    passthrough = [ep for ep in episodes if ep.event_type == PASSTHROUGH_EVENT_TYPE]
    episodes = [ep for ep in episodes if ep.event_type != PASSTHROUGH_EVENT_TYPE]
    passthrough_facts = [_passthrough_fact(ep) for ep in passthrough]

    if not episodes:
        # Nothing left for the model to read. Returning here is not just an
        # optimisation: it is the common case for a Custodian sweep, and it
        # keeps a scheduled hygiene run from spending model tokens restating
        # claims that arrived already stated.
        return DistillationResult(facts=passthrough_facts, episodes_considered=len(passthrough))

    valid_indices = {ep.index for ep in episodes}
    user_prompt = _render_user_prompt(episodes)

    raw = await chat.complete_json(system=_PROMPT, user=user_prompt, temperature=0.0)

    if not isinstance(raw, list):
        log.warning("distillation returned non-list JSON; treating as zero facts: %r", raw)
        return DistillationResult(facts=[], episodes_considered=len(episodes), dropped_invalid=1)

    facts: list[DistilledFact] = []
    dropped_invalid = 0
    dropped_no_source = 0

    # A slightly-over-limit response is tolerated before truncating, rather
    # than rejecting the whole batch for one instruction-following slip.
    for item in raw[: max_facts * 2]:
        try:
            candidate = DistilledFact.model_validate(item)
        except (ValidationError, TypeError) as exc:
            log.warning("dropped malformed fact from distillation: %s", exc)
            dropped_invalid += 1
            continue

        resolved = [i for i in candidate.source_indices if i in valid_indices]
        if not resolved:
            # Exactly the case the spec calls out: a fact whose cited sources
            # don't resolve is dropped, logged, and counted — never written
            # with a dangling provenance edge.
            log.warning("dropped fact with no resolvable source_indices: %r", candidate.fact_text)
            dropped_no_source += 1
            continue
        if len(resolved) != len(candidate.source_indices):
            candidate = candidate.model_copy(update={"source_indices": resolved})

        facts.append(candidate)
        if len(facts) >= max_facts:
            break

    return DistillationResult(
        facts=passthrough_facts + facts,
        episodes_considered=len(episodes) + len(passthrough),
        dropped_no_source=dropped_no_source,
        dropped_invalid=dropped_invalid,
    )


__all__ = [
    "DELIMITER_CLOSE",
    "DELIMITER_OPEN",
    "MAX_FACTS_PER_SESSION",
    "ChatClient",
    "DistillationResult",
    "DistilledFact",
    "EpisodeInput",
    "LLMError",
    "distill_session",
]
