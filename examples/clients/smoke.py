"""Prove a Mnemos deployment works, and that its guarantees are real.

    uv run python examples/clients/smoke.py

Reads MNEMOS_API_URL and MNEMOS_API_KEY. Exits non-zero if anything that
should hold does not — so it is usable as a deployment gate, not just a demo.

Each check is chosen because it would catch a specific way the system could be
quietly broken while still looking alive:

  posture      the cluster, not the config, says the API cannot delete
  tools        all fourteen registered
  write        a correctly-homed subject accepts an episode
  residency    a foreign-homed subject is refused, and the refusal is logged
  scope        a write key cannot reach a destructive tool
  ledger       every audit chain still recomputes
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PASS, FAIL = "  \033[32mPASS\033[0m", "  \033[31mFAIL\033[0m"


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def __call__(self, ok: bool, name: str, detail: str = "") -> None:
        print(f"{PASS if ok else FAIL}  {name}{f' — {detail}' if detail else ''}")
        if not ok:
            self.failures.append(name)


async def main() -> int:
    url = os.environ.get("MNEMOS_API_URL", "http://localhost:8000").rstrip("/")
    key = os.environ.get("MNEMOS_API_KEY")
    if not key:
        print(
            "MNEMOS_API_KEY is not set. Mint one:\n"
            "  mnemos-api mint-key --tenant clinic --scope write --label smoke",
            file=sys.stderr,
        )
        return 2

    check = Checks()
    print(f"\nMnemos smoke test — {url}\n")

    async with httpx2.AsyncClient(timeout=90.0) as anon:
        health = (await anon.get(f"{url}/health")).json()
    posture = health.get("posture", {})
    check(health.get("status") == "ok", "service is up")
    check(health.get("database") == "ok", "database reachable")
    check(
        posture.get("privilege_separation_source") == "measured",
        "privilege separation is measured, not assumed",
        f"source={posture.get('privilege_separation_source')}",
    )
    check(
        posture.get("api_can_delete") is False,
        "the API's database role holds no DELETE (invariant 1)",
        f"db_user={posture.get('db_user')}",
    )

    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=90.0)
    async with client:
        async with streamable_http_client(f"{url}/mcp", http_client=client) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()

                tools = await session.list_tools()
                check(len(tools.tools) == 14, "14 tools registered", f"got {len(tools.tools)}")

                # source_trust="agent" — a write key cannot declare "operator"
                # or "system" (those are trusted-on-arrival and require an
                # admin credential; see the corroboration gate). This is a
                # correctly-homed *agent* write, not an operator one.
                written = await session.call_tool(
                    "remember",
                    {
                        "subject_key": "staff:smoke-test",
                        "content": "Smoke test episode — safe to ignore.",
                        "event_type": "note",
                        "source_trust": "agent",
                    },
                )
                check(not written.is_error, "remember accepts a correctly-homed subject")

                # `patient:eu:*` is homed in eu-central-1 by residency policy.
                foreign = await session.call_tool(
                    "remember",
                    {
                        "subject_key": "patient:eu:smoke-test",
                        "content": "Should never be written from us-east-1.",
                        "event_type": "note",
                        "source_trust": "agent",
                    },
                )
                check(
                    foreign.is_error and "residency" in foreign.content[0].text.lower(),
                    "remember refuses a foreign-homed subject (invariant 4)",
                )

                destructive = await session.call_tool(
                    "forget", {"subject_key": "staff:smoke-test", "reason": "smoke"}
                )
                check(
                    destructive.is_error and "admin" in destructive.content[0].text,
                    "a write key cannot reach a destructive tool",
                )

                verified = await session.call_tool("verify_ledger", {})
                body = json.loads(verified.content[0].text)
                check(
                    body.get("valid") is True,
                    "every audit chain recomputes",
                    f"{body.get('entries_checked')} entries across "
                    f"{body.get('shards_checked')} shards",
                )

    if check.failures:
        print(f"\n{len(check.failures)} check(s) failed: {', '.join(check.failures)}\n")
        return 1
    print("\nAll checks passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
