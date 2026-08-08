"""Per-tenant key custody — what makes `shred` mean something.

A `KeyProvider` maps a tenant to the `KeyWrapper` that encrypts and decrypts
its rows. `shred` calls `destroy(tenant_id)`, which is the ONE irreversible
step in the whole erasure spectrum: once a tenant's master key is gone, every
ciphertext it wrapped — in the live table, in MVCC history, in a backup taken
five minutes ago — is unrecoverable. `forget` alone cannot reach the last two;
this is what closes that gap (see docs/limits.md).

`LocalKeyProvider` is real key custody for dev, CI, and the local demo
verticals — not a mock. `KmsKeyProvider` is the deployed AWS path: one
customer-managed key per tenant, `destroy()` calls `ScheduleKeyDeletion`.
Three real CMKs exist for the three demo tenants (ADR-013); the key policy
grants the `mnemos` IAM user usage and `ScheduleKeyDeletion` directly, since
no separate deployed Warden execution role exists yet — that narrowing lands
with Phase 04's deployment infra.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from mnemos_engine.crypto import DecryptionFailed, DestroyedKeyWrapper, KeyWrapper, LocalKeyWrapper

if TYPE_CHECKING:
    # boto3-stubs is a dev dependency and is not installed in the deployed
    # image. Importing it unconditionally turned a type annotation into a
    # runtime import that only failed in production — caught by running the
    # Lambda image, not by any test, since the test venv has the dev group.
    from mypy_boto3_kms import KMSClient


class KeyProvider(Protocol):
    def get_wrapper(self, tenant_id: UUID) -> KeyWrapper: ...

    def destroy(self, tenant_id: UUID) -> None: ...

    def is_destroyed(self, tenant_id: UUID) -> bool: ...


class LocalKeyProvider:
    """One AES key per tenant, generated on first use, held only in memory.

    `destroy()` drops the reference and swaps in `DestroyedKeyWrapper`, so any
    ciphertext wrapped under that tenant's key becomes permanently
    unreadable — including rows this process has never seen, since the key
    material itself is gone, not a lookup path to it.

    Not durable across process restarts, which is correct for its purpose: it
    exists to prove the *mechanism* (destroy the key, lose everything it
    wrapped) in tests and local demos, not to be a production key store.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wrappers: dict[UUID, KeyWrapper] = {}
        self._destroyed: set[UUID] = set()

    def get_wrapper(self, tenant_id: UUID) -> KeyWrapper:
        with self._lock:
            if tenant_id in self._destroyed:
                return DestroyedKeyWrapper()
            if tenant_id not in self._wrappers:
                self._wrappers[tenant_id] = LocalKeyWrapper(os.urandom(32))
            return self._wrappers[tenant_id]

    def destroy(self, tenant_id: UUID) -> None:
        with self._lock:
            self._wrappers.pop(tenant_id, None)
            self._destroyed.add(tenant_id)

    def is_destroyed(self, tenant_id: UUID) -> bool:
        with self._lock:
            return tenant_id in self._destroyed


class _KmsWrapper:
    """A `KeyWrapper` backed by one AWS KMS CMK.

    `wrap`/`unwrap` call KMS directly rather than the local envelope-DEK
    dance `LocalKeyWrapper` does — KMS Encrypt/Decrypt on a 32-byte DEK is
    exactly the operation this needs, and the ciphertext blob KMS returns
    already embeds which key (and which key version, across rotations)
    produced it, so decrypt needs no extra bookkeeping.
    """

    def __init__(self, client: KMSClient, key_id: str) -> None:
        self._client = client
        self._key_id = key_id

    def wrap(self, dek: bytes) -> bytes:
        try:
            response = self._client.encrypt(KeyId=self._key_id, Plaintext=dek)
        except ClientError as exc:
            raise DecryptionFailed(f"KMS encrypt failed for {self._key_id}: {exc}") from exc
        return bytes(response["CiphertextBlob"])

    def unwrap(self, wrapped: bytes) -> bytes:
        try:
            response = self._client.decrypt(CiphertextBlob=wrapped, KeyId=self._key_id)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            unusable_key_codes = (
                "InvalidCiphertextException",
                "KMSInvalidStateException",
                "NotFoundException",
            )
            if code in unusable_key_codes:
                # The key is disabled, pending deletion, or gone — exactly the
                # state `destroy()` puts it in. Surface this as the same
                # DecryptionFailed a LocalKeyProvider tenant would raise, so
                # the engine's "content read as destroyed, not as an error"
                # behaviour (tested in test_remember_recall.py) holds
                # identically for both providers.
                raise DecryptionFailed(
                    f"KMS decrypt failed for {self._key_id} — key is unusable "
                    f"(scheduled for deletion, disabled, or gone): {exc}"
                ) from exc
            raise
        return bytes(response["Plaintext"])


class KmsKeyProvider:
    """Per-tenant AWS KMS customer-managed keys. Phase 06.4.

    `tenant_key_ids` maps each tenant to its CMK's key ID or ARN. In a fully
    deployed system this mapping would be read from tenant metadata or
    Secrets Manager; for the three demo tenants it is passed explicitly from
    `.env`, since provisioning a key-lookup service is out of scope for a
    fixed three-tenant demo.

    `destroy()` calls `ScheduleKeyDeletion` with the minimum 7-day pending
    window (AWS's floor — it cannot be shorter). This is genuinely,
    intentionally slower than `LocalKeyProvider.destroy()`, which is
    instantaneous: a real key deletion is a consequential enough action that
    AWS itself refuses to make it immediate, and Mnemos does not try to work
    around that. `is_destroyed()` therefore means "deletion has been
    scheduled and the key can no longer encrypt or decrypt" — checked via the
    key's live KeyState — not "the key material is already gone".
    """

    def __init__(self, *, tenant_key_ids: dict[UUID, str], region: str) -> None:
        self._tenant_key_ids = dict(tenant_key_ids)
        self._client = boto3.client("kms", region_name=region)

    def _key_id_for(self, tenant_id: UUID) -> str:
        try:
            return self._tenant_key_ids[tenant_id]
        except KeyError:
            raise ValueError(
                f"no KMS key configured for tenant {tenant_id} — "
                "KmsKeyProvider only serves tenants it was constructed with"
            ) from None

    def get_wrapper(self, tenant_id: UUID) -> KeyWrapper:
        return _KmsWrapper(self._client, self._key_id_for(tenant_id))

    def destroy(self, tenant_id: UUID) -> None:
        key_id = self._key_id_for(tenant_id)
        self._client.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=7)

    def is_destroyed(self, tenant_id: UUID) -> bool:
        key_id = self._key_id_for(tenant_id)
        description = self._client.describe_key(KeyId=key_id)
        return bool(description["KeyMetadata"]["KeyState"] == "PendingDeletion")
