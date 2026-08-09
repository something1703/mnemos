"""OpenAIEmbedder against a mocked HTTP layer.

No network, no API key, no cost — `urllib.request.urlopen` is monkeypatched
to a scripted fake, so these exercise the actual retry/backoff/error-mapping
logic in `embeddings.py` rather than only the happy path a real call would
take on a good day. `_MAX_ATTEMPTS` retry backoff sleeps are patched to a
no-op so the retry-exhaustion tests don't cost real wall-clock seconds.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any
from unittest.mock import patch

import pytest
from mnemos_engine.embeddings import EmbeddingError, OpenAIEmbedder


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _embedding_response(dimensions: int, count: int) -> dict[str, Any]:
    return {
        "data": [
            {"index": i, "embedding": [0.1] * dimensions}
            for i in reversed(range(count))  # out of order on purpose
        ],
        "usage": {"total_tokens": 12},
    }


@pytest.fixture(autouse=True)
def _no_real_sleep() -> None:
    with patch("mnemos_engine.embeddings.time.sleep"):
        yield


async def test_embed_empty_list_short_circuits_without_a_call() -> None:
    embedder = OpenAIEmbedder(api_key="k", model="text-embedding-3-small", dimensions=4)
    with patch("urllib.request.urlopen") as mocked:
        assert await embedder.embed([]) == []
        mocked.assert_not_called()


async def test_embed_success_reorders_by_index() -> None:
    embedder = OpenAIEmbedder(api_key="k", model="text-embedding-3-small", dimensions=4)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_embedding_response(4, 2))):
        vectors = await embedder.embed(["a", "b"])
    assert vectors == [[0.1, 0.1, 0.1, 0.1], [0.1, 0.1, 0.1, 0.1]]


async def test_embed_dimension_mismatch_raises() -> None:
    embedder = OpenAIEmbedder(api_key="k", model="text-embedding-3-small", dimensions=1024)
    with (
        patch("urllib.request.urlopen", return_value=_FakeResponse(_embedding_response(7, 1))),
        pytest.raises(EmbeddingError, match="1024"),
    ):
        await embedder.embed(["a"])


async def test_embed_count_mismatch_raises() -> None:
    embedder = OpenAIEmbedder(api_key="k", model="text-embedding-3-small", dimensions=4)
    with (
        patch("urllib.request.urlopen", return_value=_FakeResponse(_embedding_response(4, 1))),
        pytest.raises(EmbeddingError, match="asked for 2"),
    ):
        await embedder.embed(["a", "b"])


async def test_embed_retries_then_succeeds() -> None:
    embedder = OpenAIEmbedder(api_key="k", model="text-embedding-3-small", dimensions=4)
    error = urllib.error.HTTPError("url", 500, "server error", {}, None)
    error.read = lambda: b"internal error"  # type: ignore[method-assign]
    with patch(
        "urllib.request.urlopen",
        side_effect=[error, _FakeResponse(_embedding_response(4, 1))],
    ):
        vectors = await embedder.embed(["a"])
    assert vectors == [[0.1, 0.1, 0.1, 0.1]]


async def test_embed_exhausts_retries_and_raises() -> None:
    embedder = OpenAIEmbedder(api_key="k", model="text-embedding-3-small", dimensions=4)
    error = urllib.error.URLError("connection refused")
    with (
        patch("urllib.request.urlopen", side_effect=error),
        pytest.raises(EmbeddingError, match="could not reach"),
    ):
        await embedder.embed(["a"])


def test_dimension_property() -> None:
    embedder = OpenAIEmbedder(api_key="k", model="text-embedding-3-small", dimensions=1024)
    assert embedder.dimension == 1024
