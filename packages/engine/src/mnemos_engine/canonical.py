"""Canonical serialization for hashing.

The audit chain is only useful if a third party can recompute it. That requires
one unambiguous byte sequence per payload — so this module defines the encoding
precisely and refuses anything it cannot encode unambiguously.

The full specification, written so a stranger can reimplement the verifier in
another language, is docs/ledger.md. Keep the two in sync; the spec is the
contract, this file is one implementation of it.

Rules:

  * Objects  — keys sorted by their UTF-8 bytes, no duplicate keys.
  * Arrays   — order preserved (it is meaningful).
  * Strings  — UTF-8, JSON escaping, shortest form.
  * Integers — decimal, no leading zeros, no plus sign.
  * Floats   — REJECTED. Binary floats have no single decimal representation
               across languages; 0.1 + 0.2 is where reproducible hashing goes to
               die. Callers pass a string or a scaled integer instead.
  * Booleans — `true` / `false`.
  * Null     — `null`.
  * No insignificant whitespace anywhere.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from .errors import NotCanonical

GENESIS_HASH = b"\x00" * 32
"""prev_hash for the first entry in a shard. Thirty-two zero bytes."""


def canonicalize(value: Any) -> bytes:
    """Return the one canonical byte sequence for `value`.

    Raises NotCanonical for anything ambiguous rather than guessing.
    """
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        raise NotCanonical(
            "floats are not canonically hashable — their decimal representation "
            "varies across languages and platforms. Pass a string (e.g. '0.92') "
            "or a scaled integer instead."
        )

    if isinstance(value, Decimal):
        # Decimal has an exact decimal representation, so it is safe — but
        # normalize away trailing zeros so 1.10 and 1.1 hash identically.
        return format(value.normalize(), "f")

    if isinstance(value, str):
        return value

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, bytes):
        return value.hex()

    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise NotCanonical(
                "naive datetimes are not canonically hashable — the same wall "
                "clock means different instants in different regions, which is "
                "exactly the ambiguity a multi-region memory system cannot afford."
            )
        # Always UTC, always microseconds, always the Z suffix: one instant,
        # one spelling.
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    if isinstance(value, dict):
        keys = list(value.keys())
        if any(not isinstance(k, str) for k in keys):
            raise NotCanonical("object keys must be strings")
        if len(set(keys)) != len(keys):
            raise NotCanonical("duplicate object keys")
        return {k: _normalize(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]

    raise NotCanonical(f"no canonical encoding for {type(value).__name__}")


def payload_hash(payload: dict[str, Any]) -> bytes:
    """sha256 over the canonical encoding of an audit payload."""
    return hashlib.sha256(canonicalize(payload)).digest()


def entry_hash(payload_digest: bytes, prev: bytes) -> bytes:
    """sha256(payload_hash || prev_hash).

    Byte concatenation, not string concatenation — no separator, no encoding
    step. Both inputs are exactly 32 bytes, so the concatenation is unambiguous
    without a length prefix.
    """
    if len(payload_digest) != 32:
        raise NotCanonical(f"payload_hash must be 32 bytes, got {len(payload_digest)}")
    if len(prev) != 32:
        raise NotCanonical(f"prev_hash must be 32 bytes, got {len(prev)}")
    return hashlib.sha256(payload_digest + prev).digest()


def merkle_root(leaves: list[bytes]) -> bytes:
    """Merkle root over shard head hashes, binding all shards into one state.

    Leaves are sorted before hashing so the root depends on the set of shard
    heads and not on the order a particular client happened to read them in.

    An odd node at any level is promoted unchanged rather than duplicated. The
    duplicate-last-node convention enables CVE-2012-2459-style collisions where
    two different trees produce the same root; promotion does not.
    """
    if not leaves:
        return GENESIS_HASH

    level = sorted(leaves)
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(hashlib.sha256(level[i] + level[i + 1]).digest())
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def shard_for(subject_key: str, shard_count: int) -> int:
    """Which chain shard a subject's history lives on.

    Sharding by subject rather than round-robin means a subject's history is one
    totally-ordered chain — which is what an auditor reading a single patient's
    record needs — while tenant-wide throughput still scales with shard count.
    """
    if shard_count < 1:
        raise ValueError("shard_count must be >= 1")
    digest = hashlib.sha256(subject_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") % shard_count
