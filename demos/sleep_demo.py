#!/usr/bin/env python3
"""Phase 05.7 — the sleep cycle, end to end, on camera.

    uv run python demos/sleep_demo.py

Narrates: converse over MCP -> nothing recallable yet (facts do not exist
until distilled) -> trigger consolidation -> a fact appears at UNVERIFIED,
violet, with one provenance edge -> a second, independent session mentions
the same claim -> consolidate again -> the fact promotes to CORROBORATED
live, in front of the viewer, with two provenance edges from two different
sessions. That promotion is Video Moment #1: it is the one thing this
product does that "the LLM wrote it to a database" cannot show.

Talks to the DEPLOYED stack by default (MNEMOS_API_URL, MNEMOS_DB_URL_
PIPELINE from .env) because that is what the actual recording will show.
Point MNEMOS_API_URL at a local `mnemos-api serve` and MNEMOS_DB_URL_
PIPELINE at the local cluster to rehearse offline instead.

Costs a small, real amount of OpenAI credit — two short distillation calls
and a handful of embeddings. This is a rehearsal script, meant to be
practiced before recording, not a CI assertion: model behaviour has some
variance, so it reports what actually happened at each step rather than
asserting a specific outcome and crashing if the model classifies the second
episode as NOVEL instead of a reinforcement. If that happens, reread the
printed classification and reword the second episode to overlap more with
the first, then run again.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# --- terminal colour, no new dependency -------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
VIOLET = "\033[35m"  # unverified / contested — umbra violet, matching the console
GREEN = "\033[32m"  # corroborated / trusted
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"


def _rule(title: str = "") -> None:
    width = 78
    if title:
        pad = max(0, width - len(title) - 4)
        print(f"\n{BOLD}{CYAN}── {title} {'─' * pad}{RESET}")
    else:
        print(f"{DIM}{'─' * width}{RESET}")


def _step(n: int, text: str) -> None:
    print(f"\n{BOLD}[{n}]{RESET} {text}")


def _kv(label: str, value: object, colour: str = "") -> None:
    c = colour or ""
    r = RESET if colour else ""
    print(f"    {DIM}{label:<20}{RESET} {c}{value}{r}")


def _trust_colour(trust: str) -> str:
    return {
        "unverified": VIOLET,
        "contested": VIOLET,
        "quarantined": RED,
        "corroborated": GREEN,
        "trusted": GREEN,
    }.get(trust, "")


async def _mcp_call(
    client: httpx2.AsyncClient, url: str, tool: str, args: dict[str, object]
) -> dict[str, object]:
    async with streamable_http_client(f"{url}/mcp", http_client=client) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            text = result.content[0].text
            if result.is_error:
                print(f"    {RED}error: {text}{RESET}")
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                print(f"    {text}")
                return {}


async def _remember(client: httpx2.AsyncClient, url: str, **kwargs: object) -> dict[str, object]:
    return await _mcp_call(client, url, "remember", kwargs)


async def _recall(client: httpx2.AsyncClient, url: str, **kwargs: object) -> dict[str, object]:
    return await _mcp_call(client, url, "recall", kwargs)


def _print_recall(body: dict[str, object]) -> None:
    facts = body.get("facts", [])
    withheld = body.get("unverified_withheld", 0)
    if not facts and not withheld:
        _kv("facts", "(none — nothing distilled yet)")
        return
    if not facts and withheld:
        _kv("facts", "0 recallable", VIOLET)
        _kv("unverified_withheld", withheld, VIOLET)
        return
    for item in facts:
        trust = item["trust"]
        colour = _trust_colour(trust)
        print(f"    {colour}{BOLD}{trust.upper():<13}{RESET} {item.get('text') or '(encrypted)'}")
        _kv("confidence", f"{item['confidence']:.2f}")
        _kv("corroboration_count", item["corroboration_count"])
        breakdown = item["score_breakdown"]
        _kv(
            "score",
            f"{item['score']:.3f}  (similarity={breakdown['similarity']:.2f}, "
            f"trust_weight={breakdown['trust_weight']:.2f})",
        )
        provenance = item.get("provenance", [])
        _kv("provenance edges", len(provenance))
        for edge in provenance:
            _kv("  -> episode", edge["event_id"])


async def main() -> int:
    api_url = os.environ.get("MNEMOS_API_URL", "http://localhost:8000").rstrip("/")
    api_key = os.environ.get("MNEMOS_API_KEY")
    if not api_key:
        print(
            "MNEMOS_API_KEY is not set (needs write scope). Mint one:\n"
            "  mnemos-api mint-key --tenant clinic --scope write --label sleep-demo",
            file=sys.stderr,
        )
        return 2

    subject = f"staff:sleep-demo-{uuid.uuid4().hex[:8]}"

    print(f"{BOLD}Mnemos — the sleep cycle{RESET}")
    print(f"{DIM}subject: {subject}   api: {api_url}{RESET}")

    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, timeout=90.0)
    async with client:
        _rule("moment 1 — converse")
        _step(1, "An agent-summarised conversation turn, written to episodic memory.")
        episode_1 = "Dr. Okafor is taking over the Thursday night cardiology on-call rotation."
        print(f'    {DIM}"{episode_1}"{RESET}')
        await _remember(
            client,
            api_url,
            subject_key=subject,
            content=episode_1,
            event_type="note",
            source_trust="agent",
        )

        _step(2, "recall() immediately after — before consolidation has run.")
        before = await _recall(
            client, api_url, subject_key=subject, query="Thursday on-call cardiology"
        )
        _print_recall(before)
        print(
            f"    {DIM}Nothing recallable yet. There is nothing to withhold either — "
            f"no fact exists until the sleep cycle distills one.{RESET}"
        )

        _rule("moment 2 — consolidation")
        _step(3, "Triggering consolidation directly (the same code the nightly Lambda runs).")
        outcome_1 = await _run_consolidation(subject)
        _kv("novel", outcome_1.get("novel", 0), GREEN)
        _kv("dropped", outcome_1.get("dropped", 0))

        _step(4, "recall() again, this time asking to see unverified facts too.")
        after_1 = await _recall(
            client,
            api_url,
            subject_key=subject,
            query="Thursday on-call cardiology",
            include_unverified=True,
        )
        _print_recall(after_1)
        print(
            f"    {VIOLET}A single agent-sourced episode lands UNVERIFIED. One "
            f"provenance edge, one session — not enough to earn trust on its own.{RESET}"
        )

        _rule("moment 3 — independent corroboration")
        _step(5, "A second, independent session — different source, same claim.")
        episode_2 = (
            "Scheduling export: Dr. Okafor is taking over the Thursday night "
            "cardiology on-call rotation."
        )
        print(f'    {DIM}"{episode_2}"{RESET}')
        await _remember(
            client,
            api_url,
            subject_key=subject,
            content=episode_2,
            event_type="note",
            source_trust="external",
        )

        _step(6, "Consolidating again.")
        outcome_2 = await _run_consolidation(subject)
        _kv("novel", outcome_2.get("novel", 0))
        _kv("reinforced", outcome_2.get("reinforced", 0), GREEN)
        _kv("contested", outcome_2.get("contested", 0), YELLOW)
        _kv("superseded", outcome_2.get("superseded", 0), YELLOW)

        _step(7, "recall() one more time.")
        after_2 = await _recall(
            client, api_url, subject_key=subject, query="Thursday on-call cardiology"
        )
        _print_recall(after_2)

        facts = after_2.get("facts", [])
        if facts and facts[0]["trust"] in ("corroborated", "trusted"):
            _rule("this is the moment")
            print(
                f"    {GREEN}{BOLD}Promoted live.{RESET} Two independent sessions, two "
                f"different untrusted origins, zero human intervention — and the "
                f"fact is now recallable by default. corroboration_count="
                f"{facts[0]['corroboration_count']}."
            )
        else:
            _rule("this run did not land on reinforcement")
            print(
                f"    {YELLOW}The model classified the second episode differently this "
                f"time (see outcome_2 above) — this is a rehearsal script, not a fixed "
                f"script. Reword episode_2 to overlap more with episode_1's wording and "
                f"rerun.{RESET}"
            )

        _rule("proof this is still auditable")
        _step(8, "verify_ledger() — every write above is in the hash chain.")
        verify = await _mcp_call(client, api_url, "verify_ledger", {})
        _kv("valid", verify.get("valid"), GREEN if verify.get("valid") else RED)
        _kv("entries_checked", verify.get("entries_checked"))

    return 0


async def _run_consolidation(subject: str) -> dict[str, int]:
    """Consolidate directly against the database, in-process — the same
    consolidate_batch() the deployed Lambda calls, not a shelled-out CLI, so
    this demo has one fewer moving part to fail on stage."""
    from mnemos_sleep_cycle.cli import run_consolidation
    from mnemos_sleep_cycle.runtime import build_runtime

    runtime = await build_runtime()
    try:
        return await run_consolidation(runtime, limit=25)
    finally:
        await runtime.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
