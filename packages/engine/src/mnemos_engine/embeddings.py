"""Embedding providers.

An interface with two implementations: `FakeEmbedder` (deterministic, for CI and
local work) and `TitanEmbedder` (Bedrock, wired in Phase 05). The engine never
knows which it holds, so no test needs network access or model credentials to
exercise the retrieval plumbing.

The fake is deterministic rather than random on purpose: a test that seeds
different vectors on each run cannot assert on ranking, and a ranking test that
cannot assert on ranking quietly becomes a smoke test.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

EMBED_DIM = 1024
"""Titan Embed v2 output dimension. The VECTOR(1024) column shape depends on it."""


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimension(self) -> int: ...


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


class TitanEmbedder:
    """Amazon Titan Embed Text v2 via Bedrock. Wired fully in Phase 05.

    Kept behind the same Protocol so swapping it in changes one construction
    site and no query code.
    """

    def __init__(self, client: object, model_id: str = "amazon.titan-embed-text-v2:0") -> None:
        self._client = client
        self._model_id = model_id

    @property
    def dimension(self) -> int:
        return EMBED_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("wired in Phase 05.2 alongside the consolidation Lambda")


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
