"""Per-tenant key custody — what makes `shred` mean something.

A `KeyProvider` maps a tenant to the `KeyWrapper` that encrypts and decrypts
its rows. `shred` calls `destroy(tenant_id)`, which is the ONE irreversible
step in the whole erasure spectrum: once a tenant's master key is gone, every
ciphertext it wrapped — in the live table, in MVCC history, in a backup taken
five minutes ago — is unrecoverable. `forget` alone cannot reach the last two;
this is what closes that gap (see docs/limits.md).

`LocalKeyProvider` is real key custody for dev, CI, and the local demo
verticals — not a mock. `KmsKeyProvider` is the deployed AWS path: one
customer-managed key per tenant, `destroy()` calls `ScheduleKeyDeletion`. Its
implementation is deferred to Phase 06.4, because creating real KMS resources
needs the explicit budget approval AGENTS.md requires before any AWS spend.
"""

from __future__ import annotations

import os
import threading
from typing import Protocol
from uuid import UUID

from mnemos_engine.crypto import DestroyedKeyWrapper, KeyWrapper, LocalKeyWrapper


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


class KmsKeyProvider:
    """Per-tenant AWS KMS customer-managed key. Phase 06.4.

    Deliberately not implemented here: creating a KMS CMK per tenant is a
    real, billed AWS resource, and AGENTS.md requires the user's explicit
    approval of the key policy before one is created (see PHASE_06 "Inputs
    needed from the user", item 1).
    """

    def __init__(self, *, region: str) -> None:
        self._region = region

    def get_wrapper(self, tenant_id: UUID) -> KeyWrapper:
        raise NotImplementedError(
            "KMS key provisioning requires user approval of the key policy — "
            "see PHASE_06_GOVERNANCE_WARDEN.md, 'Inputs needed from the user'"
        )

    def destroy(self, tenant_id: UUID) -> None:
        raise NotImplementedError("wired in Phase 06.4 alongside KMS provisioning")

    def is_destroyed(self, tenant_id: UUID) -> bool:
        raise NotImplementedError("wired in Phase 06.4 alongside KMS provisioning")
