"""OpenAI embeddings, behind the engine's existing `Embedder` Protocol.

Deliberately implemented with `urllib` rather than the `openai` SDK. Two
reasons, and the second is the real one:

1. It is one POST. The SDK would be a large dependency for a single endpoint.
2. **`packages/warden` must never gain a model client** (invariant 1, enforced
   by `make no-model-in-warden` over the transitive import graph). Keeping the
   provider integration to the standard library means the dependency cannot
   drift into shared packages by accident — there is nothing to drift.

Verified against the live API before being relied upon: `text-embedding-3-small`
with `dimensions=1024` returns exactly 1024 floats, matching the existing
`VECTOR(1024)` column with no migration (see scripts/pick_model.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("mnemos.api.embedding")

_ENDPOINT = "https://api.openai.com/v1/embeddings"
_TIMEOUT_SECONDS = 30
_MAX_ATTEMPTS = 3


class EmbeddingError(RuntimeError):
    """The embedding provider could not be reached or refused the request.

    Surfaced rather than swallowed: silently returning a zero vector would
    poison the index with a value that is *plausibly* near everything, which
    is far worse than a failed request.
    """


class OpenAIEmbedder:
    """Batching embedder that enforces the dimension contract on every call.

    The dimension assertion is not paranoia. The schema is `VECTOR(1024)` with
    data already in it; a provider that silently returned 1536 would either
    error deep inside an INSERT or, worse, be truncated somewhere and corrupt
    similarity comparisons against existing rows.
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
                import time

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
