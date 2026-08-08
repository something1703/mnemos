#!/usr/bin/env python3
"""Ask the OpenAI key what it can actually reach, and verify the embedding fit.

Written because guessing model IDs from memory is exactly the kind of thing
that silently breaks: model lineups move faster than any assistant's training
data, and a hardcoded ID that 404s at demo time is a self-inflicted wound.
`/v1/models` is free to call and authoritative for a given key, so this asks
rather than assumes.

What it does NOT do: report pricing. The API does not expose it. Availability
is verifiable here; cost has to come from platform.openai.com/docs/pricing.

    uv run python scripts/pick_model.py                # list what's reachable
    uv run python scripts/pick_model.py --probe MODEL  # 1-token liveness check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API = "https://api.openai.com/v1"

# Substrings that suggest a model is embedding-shaped vs chat-shaped. Only used
# for grouping the output; the authoritative list is whatever the API returns.
EMBED_HINTS = ("embedding", "embed")
NON_CHAT_HINTS = (
    "embedding",
    "embed",
    "whisper",
    "tts",
    "dall-e",
    "moderation",
    "audio",
    "image",
    "realtime",
    "transcribe",
    "search",
    "similarity",
    "edit",
)


def _request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("OPENAI_API_KEY is not set. Add it to .env, then: set -a; source .env; set +a")

    data = json.dumps(payload).encode() if payload is not None else None
    # S310: the URL is a constant HTTPS API host with a fixed path, not user input.
    req = urllib.request.Request(  # noqa: S310
        f"{API}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310
            result: dict[str, Any] = json.loads(response.read())
            return result
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        sys.exit(f"HTTP {exc.code} from {path}:\n{body}")
    except urllib.error.URLError as exc:
        sys.exit(f"could not reach {API}: {exc}")


def list_models() -> list[str]:
    return sorted(m["id"] for m in _request("/models").get("data", []))


def probe_embedding(model: str, dimensions: int) -> None:
    """Confirm the embedding model returns EXACTLY `dimensions` floats.

    This is the check that matters for us: the schema is VECTOR(1024), already
    populated. A model that cannot produce 1024 means a migration and a
    re-seed, so it is worth one near-free API call to find out now rather than
    halfway through wiring the sleep cycle.
    """
    result = _request(
        "/embeddings",
        {"model": model, "input": "mnemos dimension probe", "dimensions": dimensions},
    )
    got = len(result["data"][0]["embedding"])
    used = result.get("usage", {}).get("total_tokens", "?")
    if got != dimensions:
        sys.exit(f"  {model}: returned {got} dimensions, need exactly {dimensions}")
    print(f"  OK  {model} -> {got} dimensions (probe cost: {used} tokens)")


def probe_chat(model: str, max_tokens: int = 2000) -> None:
    """Liveness check that also measures REASONING token spend.

    The budget-relevant detail: reasoning models bill internal thinking as
    output tokens, so a "cheap per-token" model can still be expensive per
    call. A 1-token probe is not just useless here, it actively fails — the
    model cannot finish thinking in one token. So this asks a trivial question
    with real headroom and reports the reasoning/visible split, which is the
    number that actually predicts cost on a $5 budget.
    """
    try:
        result = _request(
            "/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                "max_completion_tokens": max_tokens,
            },
        )
    except SystemExit as exc:
        print(f"  FAIL  {model}: {exc}")
        return

    usage = result.get("usage", {})
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    reasoning = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
    visible = completion - reasoning
    content = (result["choices"][0]["message"].get("content") or "").strip()

    print(f"  OK  {model}")
    print(f"      reply:     {content[:60]!r}")
    print(f"      prompt:    {prompt} tokens")
    print(f"      output:    {completion} tokens ({reasoning} reasoning + {visible} visible)")
    if reasoning:
        print(
            f"      NOTE: {reasoning} reasoning tokens billed as output for a trivial\n"
            f"            prompt — budget accordingly, or use a non-reasoning model\n"
            f"            for the high-volume distillation calls."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", metavar="MODEL", help="liveness-check one chat model")
    parser.add_argument("--embed-probe", metavar="MODEL", help="verify embedding dimensions")
    parser.add_argument("--dimensions", type=int, default=1024)
    parser.add_argument("--grep", help="only show model ids containing this substring")
    args = parser.parse_args()

    if args.probe:
        probe_chat(args.probe)
        return 0
    if args.embed_probe:
        probe_embedding(args.embed_probe, args.dimensions)
        return 0

    models = list_models()
    if args.grep:
        models = [m for m in models if args.grep.lower() in m.lower()]

    embeddings = [m for m in models if any(h in m for h in EMBED_HINTS)]
    chat = [m for m in models if not any(h in m for h in NON_CHAT_HINTS)]
    other = [m for m in models if m not in embeddings and m not in chat]

    print(f"{len(models)} model(s) reachable with this key\n")
    print("EMBEDDING")
    for m in embeddings:
        print(f"  {m}")
    print("\nCHAT-CAPABLE (best guess by name)")
    for m in chat:
        print(f"  {m}")
    if other:
        print("\nOTHER")
        for m in other:
            print(f"  {m}")

    print(
        "\nPricing is not exposed by the API — check platform.openai.com/docs/pricing.\n"
        "Verify a specific model before committing to it:\n"
        "  uv run python scripts/pick_model.py --probe <chat-model>\n"
        "  uv run python scripts/pick_model.py --embed-probe text-embedding-3-small"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
