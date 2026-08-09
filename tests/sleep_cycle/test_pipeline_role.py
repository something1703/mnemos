"""The pipeline role must actually see across tenants when connected as its
own login user — not just when the migration says its role carries
`BYPASSRLS`.

This is the regression for a real bug: `mnemos_pipeline_svc` was granted
`mnemos_pipeline` (which has `BYPASSRLS`) via `GRANT mnemos_pipeline TO
mnemos_pipeline_svc`, and connecting as that login saw zero rows on
`find_unconsolidated_batches` against a cluster where the admin login plainly
saw thirteen. `BYPASSRLS`, like `SUPERUSER`, is a role *attribute*, not a
table privilege — it does not propagate through `GRANT role TO user`
membership. The fix is `ALTER USER ... BYPASSRLS` directly on the login
(`tests/conftest.py::_provision_role_users`, `db/scripts/provision_users.py`
for real deployments). Nothing else in this suite would have caught it: every
other sleep-cycle test connects through the session-scoped `db` fixture,
which is `root` — a superuser that bypasses RLS on its own regardless of any
role grant, so it could never have shown this gap.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from mnemos_engine.db import Database
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op
from mnemos_sleep_cycle.consolidate import find_unconsolidated_batches

from .conftest import LOCAL_DSN


@pytest.fixture
async def pipeline_db() -> Database:
    """Connected as `test_pipeline_user`, not `root` — the whole point of
    this test is to prove what THIS login can see, and `root`'s own
    superuser bypass would hide the exact gap being regression-tested."""
    url = LOCAL_DSN.replace("//root@", "//test_pipeline_user@")
    database = Database(url, min_size=1, max_size=4)
    try:
        await database.open()
    except Exception as exc:
        pytest.skip(f"local CockroachDB unavailable ({exc}). Run: make db-local")
    yield database
    await database.close()


async def test_pipeline_login_bypasses_rls_directly(pipeline_db: Database) -> None:
    async def whoami(cur: psycopg.AsyncCursor) -> str:
        await cur.execute("SELECT current_user")
        row = await cur.fetchone()
        return str(row[0]) if row else ""

    current_user = await pipeline_db.transaction(None, whoami, label="whoami", read_only=True)
    assert current_user == "test_pipeline_user"


async def test_pipeline_role_sees_unconsolidated_episodes_across_tenants(
    db: Database, pipeline_db: Database, tenant: uuid.UUID
) -> None:
    """Write an episode as root (any writer), then confirm the PIPELINE LOGIN
    — not root — can find it via the exact query `run-consolidation` uses in
    production. A `mnemos_pipeline`-granted login that cannot see this is the
    bug this file exists to catch."""
    session_id = uuid.uuid4()
    subject = "patient:us:pipeline-role-regression"

    # Protected by migration 010's require_audit trigger, so it needs a real
    # ticket — append_audit(), same as every other write in this codebase.
    async def write_with_audit(cur: psycopg.AsyncCursor) -> None:
        await append_audit(
            cur, tenant, op=Op.REMEMBER, actor="test", subject_key=subject, payload={}
        )
        await cur.execute(
            "INSERT INTO mnemos.episodic_events "
            "(tenant_id, subject_key, event_id, home_region, session_id, event_type, "
            " content_ciphertext, content_dek_wrapped, content_hash, source_trust) "
            "VALUES (%s, %s, %s, 'us-east-1', %s, 'note', %s, %s, %s, 'agent')",
            (tenant, subject, uuid.uuid4(), session_id, b"\x00", b"\x00", b"\x00"),
        )

    await db.transaction(tenant, write_with_audit, label="write_episode")

    async def gather(cur: psycopg.AsyncCursor) -> list:
        return await find_unconsolidated_batches(cur, limit=1000)

    batches = await pipeline_db.transaction(None, gather, label="find_batches", read_only=True)
    matching = [b for b in batches if b.tenant_id == tenant and b.subject_key == subject]
    assert matching, (
        "the pipeline login found zero batches for a tenant/subject it just wrote to — "
        "BYPASSRLS is not active for this login (see this file's module docstring)"
    )
