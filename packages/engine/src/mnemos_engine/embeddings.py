"""Embedding providers.

An interface with two implementations: `FakeEmbedder` (deterministic, for CI and
local work) and `OpenAIEmbedder` (real vectors, used by the API and the sleep
cycle). The engine never knows which it holds, so no test needs network access
or model credentials to exercise the retrieval plumbing.

The fake is deterministic rather than random on purpose: a test that seeds
different vectors on each run cannot assert on ranking, and a ranking test that
cannot assert on ranking quietly becomes a smoke test.

`OpenAIEmbedder` lives here rather than in `services/api` because the sleep
cycle needs the identical provider — a consolidation run embedding facts with a
different model or dimension than `recall` searches with would silently break
retrieval, and putting both call sites through one implementation is the only
way that mistake cannot happen. It must never migrate into `packages/warden`:
`make no-model-in-warden` enforces that at the import-graph level, not just by
convention.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

log = logging.getLogger("mnemos.embeddings")

EMBED_DIM = 1024
"""Default output dimension. The VECTOR(1024) column shape depends on it —
OpenAIEmbedder can be constructed with a different value, but doing so needs a
matching migration."""

_ENDPOINT = "https://api.openai.com/v1/embeddings"
_TIMEOUT_SECONDS = 30
_MAX_ATTEMPTS = 3


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimension(self) -> int: ...


class EmbeddingError(RuntimeError):
    """The embedding provider could not be reached or refused the request.

    Surfaced rather than swallowed: silently returning a zero vector would
    poison the index with a value that is *plausibly* near everything, which is
    far worse than a failed request.
    """


class FakeEmbedder:
    """Deterministic pseudo-embeddings derived from the text.

    Not semantically meaningful — "cat" and "kitten" are no closer than "cat"
    and "hydraulics". That is fine for what CI asserts (isolation, transactional
    deletion of index entries, score plumbing) and useless for what it does not
    assert (retrieval quality), which is measured against real embeddings in the
    Phase 05 golden set.

    One property IS meaningful: identical text yields an identical vector, so
    near-duplicate detection can be tested without a model.
    """

    def __init__(self, dimension: int = EMBED_DIM) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        digest = hashlib.sha512(text.encode("utf-8")).digest()
        raw = [((digest[i % len(digest)] * (i + 7)) % 257) - 128 for i in range(self._dimension)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]


class OpenAIEmbedder:
    """Batching embedder that enforces the dimension contract on every call.

    The dimension assertion is not paranoia. The schema is `VECTOR(1024)` with
    data already in it; a provider that silently returned 1536 would either
    error deep inside an INSERT or, worse, be truncated somewhere and corrupt
    similarity comparisons against existing rows.

    Deliberately implemented with `urllib` rather than the `openai` SDK — one
    POST does not justify a dependency, and keeping the provider integration to
    the standard library means it cannot drift into `packages/warden` by
    accident (there is nothing to drift).
    """

    def __init__(self, *, api_key: str, model: str, dimensions: int) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions

    @property
    def dimension(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # One request for the whole batch — the endpoint accepts a list, and
        # per-text requests would multiply latency and overhead for no gain.
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self._model,
            "input": texts,
            "dimensions": self._dimensions,
        }
        body = json.dumps(payload).encode("utf-8")

        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                result = self._post(body)
                break
            except EmbeddingError as exc:
                last_error = exc
                if attempt == _MAX_ATTEMPTS:
                    raise
                delay = 0.5 * (2 ** (attempt - 1))
                log.warning(
                    "embedding attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)
        else:  # pragma: no cover - loop always breaks or raises
            raise EmbeddingError(str(last_error))

        vectors = [item["embedding"] for item in sorted(result["data"], key=lambda d: d["index"])]

        if len(vectors) != len(texts):
            raise EmbeddingError(f"asked for {len(texts)} embeddings, received {len(vectors)}")
        for vector in vectors:
            if len(vector) != self._dimensions:
                raise EmbeddingError(
                    f"{self._model} returned {len(vector)} dimensions, "
                    f"schema requires exactly {self._dimensions}"
                )

        usage = result.get("usage", {})
        log.debug("embedded %d text(s), %s tokens", len(texts), usage.get("total_tokens", "?"))
        return vectors

    def _post(self, body: bytes) -> dict[str, Any]:
        request = urllib.request.Request(
            _ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
                parsed: dict[str, Any] = json.loads(response.read())
                return parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise EmbeddingError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise EmbeddingError(f"could not reach the embedding endpoint: {exc}") from exc


def to_pgvector(vector: list[float]) -> str:
    """Render a vector in the literal form CockroachDB's VECTOR type parses.

    Six decimal places: enough to preserve ranking, short enough that a 1024-dim
    literal stays a reasonable size on the wire.
    """
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = 60) -> dict[str, float]:
    """Fuse several ranked ID lists into one score per ID.

    RRF uses only rank position, never the underlying scores — which is the
    point. Cosine similarity and BM25-style text relevance are not on comparable
    scales, and normalising them against each other requires assumptions that
    quietly break when the corpus changes. Rank is the one thing both agree on.

    k=60 is the value from the original RRF paper; it damps the influence of the
    very top rank so a single confident retriever cannot dominate the fusion.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for position, identifier in enumerate(ranking, start=1):
            fused[identifier] = fused.get(identifier, 0.0) + 1.0 / (k + position)
    return fused
