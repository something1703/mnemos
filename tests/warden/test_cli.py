"""`mnemos-attest`'s CLI surface: argument validation and tenant resolution.

Everything here runs against the real local cluster and zero AWS — the
S3-touching happy paths (`anchor`/`verify`/`presign` actually reaching S3)
are `@pytest.mark.aws` in test_attestation.py, same reasoning as
KmsKeyProvider: this file covers what genuinely needs no live AWS credential,
which turns out to be most of the argument-handling surface a user actually
hits first (a missing --url, a missing --bucket, a mistyped --tenant).
Before this file, cli.py had no test importing it at all.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from mnemos_engine.ledger import checkpoint as take_checkpoint
from mnemos_engine.models import SourceTrust
from mnemos_warden.cli import (
    EXIT_ERROR,
    EXIT_MISMATCH,
    EXIT_OK,
    _cmd_anchor,
    _cmd_presign,
    _cmd_verify,
    _resolve_tenant,
    main,
)

BUCKET = "mnemos-ledger-anchor-test"


@pytest.fixture
def s3() -> MagicMock:
    return MagicMock()


async def _checkpointed_tenant(db, engine_for, tenant: uuid.UUID) -> int:
    """A tenant with one real audit entry and one real checkpoint over it —
    the minimum state `anchor`/`verify`/`presign` all need to do anything."""
    engine = engine_for(tenant)
    await engine.remember(
        tenant,
        subject_key="patient:cli-test",
        session_id=uuid.uuid4(),
        event_type="note",
        content="one real episode for the CLI happy-path tests",
        source_trust=SourceTrust.OPERATOR,
    )

    async def take(cur):
        return await take_checkpoint(cur, tenant, actor="test")

    cp = await db.transaction(tenant, take, label="checkpoint")
    return cp.checkpoint_seq


async def test_resolve_tenant_with_uuid_string_short_circuits(db) -> None:
    """A UUID input never touches the database at all — parseable as a UUID
    is treated as authoritative."""
    random_id = uuid.uuid4()
    resolved = await _resolve_tenant(db, str(random_id))
    assert resolved == random_id


async def test_resolve_tenant_with_known_slug(db, tenant: uuid.UUID) -> None:
    async def read_slug(cur: Any) -> str:
        await cur.execute("SELECT slug FROM mnemos.tenants WHERE tenant_id = %s", (tenant,))
        row = await cur.fetchone()
        return str(row[0])

    slug = await db.transaction(None, read_slug, label="read_slug", read_only=True)
    resolved = await _resolve_tenant(db, slug)
    assert resolved == tenant


async def test_resolve_tenant_with_unknown_slug_returns_none(db) -> None:
    resolved = await _resolve_tenant(db, "no-such-tenant-slug-ever")
    assert resolved is None


async def test_cmd_anchor_unknown_tenant_is_an_error_before_touching_s3(db) -> None:
    """The tenant lookup fails first, so a bogus S3 client (never called) is
    fine here — proving the DB-first order, not exercising S3 at all."""
    exit_code = await _cmd_anchor(db, None, "some-bucket", "no-such-tenant-slug-ever")
    assert exit_code == EXIT_ERROR


async def test_cmd_verify_unknown_tenant_is_an_error(db) -> None:
    exit_code = await _cmd_verify(db, None, "some-bucket", "no-such-tenant-slug-ever", None)
    assert exit_code == EXIT_ERROR


async def test_cmd_presign_unknown_tenant_is_an_error(db) -> None:
    exit_code = await _cmd_presign(db, None, "some-bucket", "no-such-tenant-slug-ever", None, 3600)
    assert exit_code == EXIT_ERROR


async def test_cmd_anchor_with_no_checkpoint_yet_is_an_error(db, tenant: uuid.UUID) -> None:
    """A real tenant, zero checkpoints taken — still resolves via DB alone,
    still never reaches S3."""
    exit_code = await _cmd_anchor(db, None, "some-bucket", str(tenant))
    assert exit_code == EXIT_ERROR


def test_main_without_a_database_url_fails_before_connecting(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MNEMOS_DB_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["mnemos-attest", "--url", "", "anchor", "--tenant", "clinic"])
    assert main() == EXIT_ERROR
    assert "No database URL" in capsys.readouterr().err


def test_main_without_a_bucket_fails_before_connecting(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MNEMOS_S3_ANCHOR_BUCKET", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemos-attest",
            "--url",
            "postgresql://root@localhost:26257/mnemos?sslmode=disable",
            "--bucket",
            "",
            "anchor",
            "--tenant",
            "clinic",
        ],
    )
    assert main() == EXIT_ERROR
    assert "No anchor bucket" in capsys.readouterr().err


def test_main_rejects_the_placeholder_url_from_env_example(monkeypatch, capsys) -> None:
    """.env.example ships `postgresql://<user>:...` as a template value. If a
    judge sources it unedited, this must fail with a clear message, not a
    confusing connection-refused deep inside psycopg."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemos-attest",
            "--url",
            "postgresql://<user>:<password>@<host>:26257/defaultdb?sslmode=verify-full",
            "verify",
            "--tenant",
            "clinic",
        ],
    )
    assert main() == EXIT_ERROR
    assert "No database URL" in capsys.readouterr().err


def test_main_requires_a_subcommand(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["mnemos-attest"])
    with pytest.raises(SystemExit):
        main()


async def test_cmd_anchor_happy_path_puts_and_returns_exit_ok(
    db, tenant, engine_for, s3, capsys
) -> None:
    await _checkpointed_tenant(db, engine_for, tenant)

    exit_code = await _cmd_anchor(db, s3, BUCKET, str(tenant))

    assert exit_code == EXIT_OK
    s3.put_object.assert_called_once()
    assert "anchored checkpoint" in capsys.readouterr().out


async def test_cmd_verify_happy_path_matches_and_returns_exit_ok(
    db, tenant, engine_for, s3, capsys
) -> None:
    await _checkpointed_tenant(db, engine_for, tenant)

    captured: dict[str, bytes] = {}

    def fake_put_object(*, Bucket, Key, Body, ContentType):  # noqa: N803 - matches boto3's kwargs
        captured["body"] = Body

    s3.put_object.side_effect = fake_put_object
    s3.get_object.return_value = {
        "Body": type("Stream", (), {"read": lambda self: captured["body"]})()
    }

    anchor_exit = await _cmd_anchor(db, s3, BUCKET, str(tenant))
    assert anchor_exit == EXIT_OK

    verify_exit = await _cmd_verify(db, s3, BUCKET, str(tenant), None)

    assert verify_exit == EXIT_OK
    assert "MATCH" in capsys.readouterr().out


async def test_cmd_verify_mismatch_returns_exit_mismatch(
    db, tenant, engine_for, s3, capsys
) -> None:
    """A forged anchor — same shard heads as the live chain, but a doctored
    merkle root — is exactly what a whole-shard rewrite consistent within
    CockroachDB itself would produce. `_cmd_verify` must report EXIT_MISMATCH,
    not raise past the CLI boundary uncaught."""
    checkpoint_seq = await _checkpointed_tenant(db, engine_for, tenant)

    async def read_heads(cur):
        await cur.execute(
            "SELECT shard_heads FROM mnemos.chain_checkpoints "
            "WHERE tenant_id = %s AND checkpoint_seq = %s",
            (tenant, checkpoint_seq),
        )
        return (await cur.fetchone())[0]

    real_heads = await db.transaction(tenant, read_heads, label="read", read_only=True)

    import json

    forged = json.dumps({"merkle_root": "0" * 64, "shard_heads": dict(real_heads)}).encode()
    s3.get_object.return_value = {"Body": type("Stream", (), {"read": lambda self: forged})()}

    exit_code = await _cmd_verify(db, s3, BUCKET, str(tenant), checkpoint_seq)

    assert exit_code == EXIT_MISMATCH
    assert "MISMATCH" in capsys.readouterr().out


async def test_cmd_presign_happy_path_prints_url_and_returns_exit_ok(
    db, tenant, engine_for, s3, capsys
) -> None:
    await _checkpointed_tenant(db, engine_for, tenant)
    s3.generate_presigned_url.return_value = "https://example.com/signed"

    exit_code = await _cmd_presign(db, s3, BUCKET, str(tenant), None, 3600)

    assert exit_code == EXIT_OK
    s3.generate_presigned_url.assert_called_once()
    out = capsys.readouterr()
    assert "https://example.com/signed" in out.out
    assert "independent_verify.py" in out.err
