"""Canonical serialization and hash construction.

Pure functions, no database. These are the most load-bearing forty lines in the
project: if two implementations disagree on the bytes, every proof Mnemos emits
is unverifiable by anyone else, and the ledger becomes a claim rather than
evidence.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from mnemos_engine.canonical import (
    GENESIS_HASH,
    canonicalize,
    entry_hash,
    merkle_root,
    payload_hash,
    shard_for,
)
from mnemos_engine.errors import NotCanonical


def test_object_keys_are_sorted_not_insertion_ordered() -> None:
    assert canonicalize({"b": 1, "a": 2}) == canonicalize({"a": 2, "b": 1}) == b'{"a":2,"b":1}'


def test_array_order_is_preserved() -> None:
    """Arrays are sequences, not sets — reordering changes meaning."""
    assert canonicalize([1, 2]) != canonicalize([2, 1])


def test_no_insignificant_whitespace() -> None:
    assert b" " not in canonicalize({"a": [1, 2], "b": {"c": 3}})


def test_floats_are_rejected() -> None:
    """0.1 + 0.2 is where reproducible hashing goes to die.

    Binary floats have no single decimal spelling across languages, so accepting
    one would make our hashes unreproducible by a verifier written in Go or Rust.
    """
    with pytest.raises(NotCanonical, match="floats"):
        canonicalize({"confidence": 0.1 + 0.2})


def test_decimals_are_accepted_and_normalized() -> None:
    assert canonicalize({"x": Decimal("1.10")}) == canonicalize({"x": Decimal("1.1")})


def test_naive_datetimes_are_rejected() -> None:
    """The same wall clock is a different instant in a different region — the
    precise ambiguity a multi-region memory system cannot tolerate."""
    with pytest.raises(NotCanonical, match="naive"):
        canonicalize({"at": datetime(2026, 8, 8, 12, 0, 0)})


def test_equivalent_instants_in_different_zones_hash_identically() -> None:
    utc = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert canonicalize({"at": utc}) == canonicalize({"at": ist})


def test_uuid_and_bytes_have_stable_encodings() -> None:
    uid = UUID("11111111-1111-4111-8111-111111111111")
    assert canonicalize({"id": uid}) == b'{"id":"11111111-1111-4111-8111-111111111111"}'
    assert canonicalize({"h": b"\x00\xff"}) == b'{"h":"00ff"}'


def test_unicode_is_utf8_not_escaped() -> None:
    """ensure_ascii would produce \\u sequences, and two encoders that disagree
    on escaping produce different hashes for the same string."""
    assert canonicalize({"k": "é"}) == '{"k":"é"}'.encode()


def test_unencodable_types_raise_rather_than_coerce() -> None:
    with pytest.raises(NotCanonical, match="no canonical encoding"):
        canonicalize({"s": {1, 2}})


def test_entry_hash_is_sha256_of_concatenated_digests() -> None:
    """Pin the construction so an independent verifier can reproduce it."""
    a, b = b"\x01" * 32, b"\x02" * 32
    assert entry_hash(a, b) == hashlib.sha256(a + b).digest()


def test_entry_hash_rejects_wrong_length_inputs() -> None:
    with pytest.raises(NotCanonical, match="32 bytes"):
        entry_hash(b"short", GENESIS_HASH)


def test_chain_is_order_sensitive() -> None:
    """Swapping two entries must change the head, or reordering history is free."""
    p1, p2 = payload_hash({"n": 1}), payload_hash({"n": 2})
    forward = entry_hash(p2, entry_hash(p1, GENESIS_HASH))
    backward = entry_hash(p1, entry_hash(p2, GENESIS_HASH))
    assert forward != backward


def test_single_bit_change_breaks_the_chain() -> None:
    original = payload_hash({"op": "forget", "subject_key": "patient:1"})
    tampered = payload_hash({"op": "forget", "subject_key": "patient:2"})
    assert entry_hash(original, GENESIS_HASH) != entry_hash(tampered, GENESIS_HASH)


def test_merkle_root_is_order_independent() -> None:
    """The root binds the SET of shard heads.

    Two clients reading shards in different orders must agree, or verification
    depends on read order rather than on state.
    """
    leaves = [hashlib.sha256(bytes([i])).digest() for i in range(5)]
    assert merkle_root(leaves) == merkle_root(list(reversed(leaves)))


def test_merkle_root_changes_when_any_leaf_changes() -> None:
    leaves = [hashlib.sha256(bytes([i])).digest() for i in range(5)]
    mutated = [*leaves[:-1], hashlib.sha256(b"tampered").digest()]
    assert merkle_root(leaves) != merkle_root(mutated)


def test_merkle_root_promotes_odd_node_rather_than_duplicating_it() -> None:
    """Duplicating a trailing node enables CVE-2012-2459-style collisions, where
    two different leaf sets produce the same root. Promotion does not."""
    a, b, c = (hashlib.sha256(bytes([i])).digest() for i in range(3))
    three = merkle_root([a, b, c])
    four_with_duplicate = merkle_root([a, b, c, c])
    assert three != four_with_duplicate


def test_empty_chain_has_genesis_root() -> None:
    assert merkle_root([]) == GENESIS_HASH


def test_shard_assignment_is_stable_and_distributed() -> None:
    assert shard_for("patient:eu:8f2c", 16) == shard_for("patient:eu:8f2c", 16)
    buckets = {shard_for(f"subject:{i}", 16) for i in range(200)}
    # Poor distribution would concentrate writes and defeat the point of sharding.
    assert len(buckets) >= 12, f"only {len(buckets)}/16 shards used"


def test_same_subject_always_lands_on_one_shard() -> None:
    """A subject's history must be one totally-ordered chain, which is what an
    auditor reading a single record needs."""
    assert len({shard_for("patient:eu:8f2c", 16) for _ in range(50)}) == 1
