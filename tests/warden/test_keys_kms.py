"""KmsKeyProvider and _KmsWrapper against a mocked boto3 KMS client.

No live AWS calls, no moto — `boto3.client("kms", ...)` is patched to return
a `MagicMock`, and each test scripts exactly what that mock returns or
raises. This is the deployed key-custody path (`docs/security.md`'s "what
crypto-shred does and does not cover" claim rests on it); before this file it
had zero test coverage at all, verified only by `LocalKeyProvider`'s
in-memory stand-in.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from mnemos_engine.crypto import DecryptionFailed
from mnemos_warden.keys import KmsKeyProvider


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "kms-op")


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_client():
    with patch("boto3.client") as mocked:
        client = MagicMock()
        mocked.return_value = client
        yield client


def _provider(
    tenant_id: uuid.UUID, key_id: str = "arn:aws:kms:us-east-1:1:key/abc"
) -> KmsKeyProvider:
    return KmsKeyProvider(tenant_key_ids={tenant_id: key_id}, region="us-east-1")


def test_get_wrapper_unknown_tenant_raises(mock_client, tenant_id: uuid.UUID) -> None:
    provider = _provider(tenant_id)
    with pytest.raises(ValueError, match="no KMS key configured"):
        provider.get_wrapper(uuid.uuid4())


def test_wrap_calls_kms_encrypt_and_returns_ciphertext_blob(
    mock_client, tenant_id: uuid.UUID
) -> None:
    mock_client.encrypt.return_value = {"CiphertextBlob": b"sealed-dek"}
    provider = _provider(tenant_id)
    wrapper = provider.get_wrapper(tenant_id)

    result = wrapper.wrap(b"\x00" * 32)

    assert result == b"sealed-dek"
    mock_client.encrypt.assert_called_once()
    assert mock_client.encrypt.call_args.kwargs["Plaintext"] == b"\x00" * 32


def test_wrap_maps_client_error_to_decryption_failed(mock_client, tenant_id: uuid.UUID) -> None:
    mock_client.encrypt.side_effect = _client_error("KMSInternalException")
    provider = _provider(tenant_id)
    wrapper = provider.get_wrapper(tenant_id)

    with pytest.raises(DecryptionFailed, match="KMS encrypt failed"):
        wrapper.wrap(b"\x00" * 32)


def test_unwrap_calls_kms_decrypt_and_returns_plaintext(mock_client, tenant_id: uuid.UUID) -> None:
    mock_client.decrypt.return_value = {"Plaintext": b"\x01" * 32}
    provider = _provider(tenant_id)
    wrapper = provider.get_wrapper(tenant_id)

    result = wrapper.unwrap(b"sealed-dek")

    assert result == b"\x01" * 32


@pytest.mark.parametrize(
    "code", ["InvalidCiphertextException", "KMSInvalidStateException", "NotFoundException"]
)
def test_unwrap_treats_unusable_key_states_as_decryption_failed(
    mock_client, tenant_id: uuid.UUID, code: str
) -> None:
    """The engine's shred contract: after destroy(), reads fail closed with
    DecryptionFailed identically to LocalKeyProvider, not with a raw AWS
    exception a caller would have to special-case."""
    mock_client.decrypt.side_effect = _client_error(code)
    provider = _provider(tenant_id)
    wrapper = provider.get_wrapper(tenant_id)

    with pytest.raises(DecryptionFailed, match="key is unusable"):
        wrapper.unwrap(b"sealed-dek")


def test_unwrap_reraises_unrelated_client_errors(mock_client, tenant_id: uuid.UUID) -> None:
    """A throttling or permissions error is not "the key is gone" and must
    not be silently reinterpreted as a successful shred."""
    mock_client.decrypt.side_effect = _client_error("ThrottlingException")
    provider = _provider(tenant_id)
    wrapper = provider.get_wrapper(tenant_id)

    with pytest.raises(ClientError):
        wrapper.unwrap(b"sealed-dek")


def test_destroy_schedules_key_deletion_with_seven_day_floor(
    mock_client, tenant_id: uuid.UUID
) -> None:
    provider = _provider(tenant_id, key_id="key-under-test")
    provider.destroy(tenant_id)

    mock_client.schedule_key_deletion.assert_called_once_with(
        KeyId="key-under-test", PendingWindowInDays=7
    )


def test_is_destroyed_true_when_key_state_is_pending_deletion(
    mock_client, tenant_id: uuid.UUID
) -> None:
    mock_client.describe_key.return_value = {"KeyMetadata": {"KeyState": "PendingDeletion"}}
    provider = _provider(tenant_id)
    assert provider.is_destroyed(tenant_id) is True


def test_is_destroyed_false_for_an_enabled_key(mock_client, tenant_id: uuid.UUID) -> None:
    mock_client.describe_key.return_value = {"KeyMetadata": {"KeyState": "Enabled"}}
    provider = _provider(tenant_id)
    assert provider.is_destroyed(tenant_id) is False


def test_destroy_unknown_tenant_raises(mock_client, tenant_id: uuid.UUID) -> None:
    provider = _provider(tenant_id)
    with pytest.raises(ValueError, match="no KMS key configured"):
        provider.destroy(uuid.uuid4())
