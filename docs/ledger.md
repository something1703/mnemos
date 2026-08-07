# The audit ledger — specification

Written so a stranger can reimplement the verifier in another language without
reading our Python. If this document and the code disagree, **this document is
the contract** and the code is a bug.

A proof only counts if someone who distrusts us can check it.

---

## 1. Canonical encoding

Every hash is taken over one unambiguous byte sequence. The encoding is JSON
with these rules:

| Type | Encoding |
|---|---|
| object | keys sorted by their **UTF-8 bytes**; duplicate keys rejected |
| array | order preserved — it is meaningful |
| string | UTF-8, standard JSON escaping, **not** `\uXXXX`-escaped |
| integer | decimal, no leading zeros, no `+` |
| float | **rejected** |
| decimal | plain decimal string, trailing zeros stripped (`1.10` → `1.1`) |
| bytes | lowercase hex |
| UUID | canonical lowercase hyphenated form |
| timestamp | UTC, `YYYY-MM-DDTHH:MM:SS.ffffffZ`, always 6 fractional digits |
| boolean | `true` / `false` |
| null | `null` |

No insignificant whitespace anywhere. Separators are exactly `,` and `:`.

**Why floats are rejected.** A binary float has no single decimal spelling
across languages; `0.1 + 0.2` serialises differently in Python, Go, and
JavaScript. Accepting one would make our hashes unreproducible by an independent
verifier, which defeats the purpose. Confidence and similarity values are
carried as decimal strings.

**Why naive timestamps are rejected.** The same wall-clock reading is a
different instant in a different region — precisely the ambiguity a
jurisdiction-aware memory system cannot afford. Every timestamp is normalised to
UTC before encoding, so two clients in different zones hash the same instant
identically.

---

## 2. Entry hashing

```
payload_hash = SHA256( canonical_encoding(payload) )
entry_hash   = SHA256( payload_hash ‖ prev_hash )
```

`‖` is raw byte concatenation. No separator, no length prefix, no re-encoding —
both operands are exactly 32 bytes, so the concatenation is unambiguous.

`prev_hash` for the first entry in a shard is **32 zero bytes** (`GENESIS`).

The `payload` committed for an entry is:

```json
{
  "actor":       "<string>",
  "data":        { ...operation-specific... },
  "op":          "<operation>",
  "reason":      "<string|null>",
  "seq":         <integer>,
  "shard_id":    <integer>,
  "subject_key": "<string|null>",
  "tenant_id":   "<uuid>"
}
```

(shown sorted, as it is encoded)

---

## 3. Sharding

```
shard_id = first_2_bytes_big_endian( SHA256(subject_key) ) mod shard_count
```

Default `shard_count` is 16.

Sharding **by subject**, not round-robin, so that one subject's history is a
single totally-ordered chain — what an auditor reading one patient's record
needs — while tenant-wide write throughput scales with shard count rather than
serialising on a single row.

Each `(tenant_id, shard_id)` has its own independent sequence starting at 1.

---

## 4. Checkpoints

A checkpoint binds every shard head into one root:

```
merkle_root = MERKLE( sorted( [entry_hash of each shard head] ) )
```

Merkle construction:

- leaves are **sorted** before hashing, so the root depends on the *set* of
  shard heads and not on the order a client read them in;
- internal node = `SHA256(left ‖ right)`;
- an odd node at any level is **promoted unchanged**, never duplicated;
- an empty set has root `GENESIS` (32 zero bytes).

**Why promotion rather than duplication.** Duplicating a trailing node allows
CVE-2012-2459-style collisions, where two different leaf sets produce the same
root. Promotion does not.

---

## 5. Verification

A complete verification is **three** independent checks. Skipping the third
makes the second vacuous — a mistake we made and caught with
`test_internally_consistent_shard_rewrite_is_caught_by_the_checkpoint`.

### 5.1 Per-entry

For each shard, walking `seq` ascending from 1:

1. `seq` must equal the expected counter — a gap means a row was removed. The
   surviving rows still chain correctly to each other, so **only the sequence
   reveals a deletion**.
2. `SHA256(canonical(payload))` must equal the stored `payload_hash` — catches
   an edited row.
3. Stored `prev_hash` must equal the previous entry's `entry_hash` — catches a
   splice.
4. `SHA256(payload_hash ‖ prev_hash)` must equal the stored `entry_hash`.

### 5.2 Checkpoint internal consistency

For each checkpoint, recomputing the Merkle root from its own recorded shard
heads must reproduce the stored `merkle_root`.

### 5.3 Checkpoint against the live chain — the check that catches a forger

For each shard head `(shard_id, seq, hash)` recorded in a checkpoint, the entry
currently at `(tenant_id, shard_id, seq)` must still have exactly that
`entry_hash`.

**This is the only check that detects an adversary who controls the database.**
Such an attacker can rewrite an entire shard *and* recompute every hash inside
it, producing a chain that passes 5.1 perfectly and a checkpoint that passes 5.2
perfectly. The forgery appears only as a divergence between what the chain holds
now and what a previously-committed root said it held.

Its strength therefore depends entirely on the root having been committed
**before** the rewrite, and — for an attacker who can also edit
`chain_checkpoints` — on the root having been anchored **outside** the database.
That is what Phase 06.6 does with S3 Object Lock, and why `docs/limits.md` says
tamper-*evident within one checkpoint epoch* rather than tamper-proof.

---

## 6. Verifying our chain yourself

```python
import hashlib, json

GENESIS = b"\x00" * 32


def canonical(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def entry_hash(payload_hash: bytes, prev: bytes) -> bytes:
    return hashlib.sha256(payload_hash + prev).digest()


# For each shard, ordered by seq:
prev = GENESIS
for row in rows:  # seq, payload, payload_hash, prev_hash, entry_hash
    ph = hashlib.sha256(canonical(row["payload"])).digest()
    assert ph == row["payload_hash"], f"edited at {row['seq']}"
    assert row["prev_hash"] == prev, f"spliced at {row['seq']}"
    eh = entry_hash(ph, prev)
    assert eh == row["entry_hash"], f"bad entry hash at {row['seq']}"
    prev = eh
```

`scripts/independent_verify.py` (Phase 06.6) is the complete version: under 100
lines, standard library plus boto3, importing nothing from `mnemos_engine`. It
fetches the anchored root from the public S3 object and verifies a live tenant
against it.

---

## 7. What the ledger does not prove

Deliberately restated here so this document cannot be read in isolation and
overinterpreted. See `docs/limits.md`.

- It proves a mutation was **recorded** and the record has not been altered. It
  does **not** prove the underlying bytes were destroyed — MVCC history and
  backups are covered by `shred`, not by the chain.
- Detection of a full-shard rewrite is bounded by the checkpoint interval.
- A principal with DDL rights can drop the enforcement trigger and write
  unaudited rows. The anchored checkpoints notice the resulting gap; the trigger
  alone would not.
