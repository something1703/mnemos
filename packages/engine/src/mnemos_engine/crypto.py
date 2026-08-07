"""Envelope encryption.

Every episode and fact body is encrypted with a per-row data key (DEK), and the
DEK is wrapped by a per-tenant master key. The plaintext DEK is never persisted.

This is what makes `shred` mean something. Destroying the tenant's master key
makes every wrapped DEK unrecoverable, and therefore every ciphertext
unreadable — including the copies sitting in backups and in MVCC history, which
`forget` alone cannot reach. Without envelope encryption, "erased" would be true
only of the live keyspace, and docs/limits.md would have a hole in it we could
not close.

The wrapper is an interface: `LocalKeyWrapper` for development and CI,
`KmsKeyWrapper` (Phase 06.4) for deployment. The rest of the engine never knows
which one it has.
"""

from __future__ import annotations

import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import MnemosError

DEK_BYTES = 32
NONCE_BYTES = 12


class DecryptionFailed(MnemosError):
    """Ciphertext could not be decrypted.

    Expected and correct after a `shred`: the master key is gone, so the DEK
    cannot be unwrapped. Callers render this as "content destroyed", never as an
    internal error — a shredded record is a successful outcome, not a fault.
    """


class KeyWrapper(Protocol):
    """Wraps and unwraps data keys. The master key never leaves this boundary."""

    def wrap(self, dek: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes) -> bytes: ...


class LocalKeyWrapper:
    """Development and CI wrapper backed by a static key.

    Deliberately NOT a no-op. If local runs stored plaintext, the encryption path
    would be exercised only in production — which is where nobody wants to
    discover that ciphertext columns and query paths disagree.
    """

    def __init__(self, master_key: bytes | None = None) -> None:
        if master_key is None:
            raw = os.environ.get("MNEMOS_LOCAL_MASTER_KEY")
            master_key = bytes.fromhex(raw) if raw else b"\x2a" * DEK_BYTES
        if len(master_key) != DEK_BYTES:
            raise ValueError(f"master key must be {DEK_BYTES} bytes")
        self._aead = AESGCM(master_key)

    def wrap(self, dek: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, dek, b"mnemos-dek")

    def unwrap(self, wrapped: bytes) -> bytes:
        try:
            return self._aead.decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], b"mnemos-dek")
        except InvalidTag as exc:
            raise DecryptionFailed(
                "data key could not be unwrapped — the master key is wrong or destroyed"
            ) from exc


class DestroyedKeyWrapper:
    """A wrapper whose master key no longer exists.

    Used to prove the crypto-shred property in tests: after `shred`, reads must
    fail closed with DecryptionFailed rather than returning garbage or, worse,
    silently succeeding against a cached key.
    """

    def wrap(self, dek: bytes) -> bytes:
        raise DecryptionFailed("master key has been destroyed; this tenant accepts no new writes")

    def unwrap(self, wrapped: bytes) -> bytes:
        raise DecryptionFailed("master key has been destroyed; ciphertext is unrecoverable")


class Envelope:
    """Encrypts and decrypts row bodies using a KeyWrapper."""

    def __init__(self, wrapper: KeyWrapper) -> None:
        self._wrapper = wrapper

    def encrypt(self, plaintext: str, *, aad: str) -> tuple[bytes, bytes]:
        """Return (ciphertext, wrapped_dek).

        `aad` binds the ciphertext to its row identity (tenant + subject), so a
        ciphertext lifted from one row and pasted into another fails to decrypt
        rather than silently succeeding. Without it, an attacker with UPDATE
        rights could move a record between subjects without touching the bytes.
        """
        dek = os.urandom(DEK_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        sealed = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return nonce + sealed, self._wrapper.wrap(dek)

    def decrypt(self, ciphertext: bytes, wrapped_dek: bytes, *, aad: str) -> str:
        dek = self._wrapper.unwrap(wrapped_dek)
        try:
            plain = AESGCM(dek).decrypt(
                ciphertext[:NONCE_BYTES], ciphertext[NONCE_BYTES:], aad.encode("utf-8")
            )
        except InvalidTag as exc:
            raise DecryptionFailed(
                "ciphertext failed authentication — it was altered, or it belongs to a "
                "different row than the one it is stored on"
            ) from exc
        return plain.decode("utf-8")


def row_aad(tenant_id: object, subject_key: str) -> str:
    """Associated data binding a ciphertext to its row."""
    return f"mnemos:{tenant_id}:{subject_key}"
