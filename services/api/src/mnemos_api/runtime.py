"""Process-wide runtime: connection pools, engine, Warden, embedder.

The important structure here is that there are **two** database pools, not
one:

  * `db` connects as a role with no DELETE grant anywhere (`mnemos_api`,
    migration 011). Every ordinary memory operation runs through it, so a bug
    in tool dispatch cannot destroy data — the database would refuse.
  * `warden_db` is the privileged connection, touched only after an admin
    scope check has already passed.

If they are the same DSN, privilege separation is not active. The service
still starts, because refusing to run would make local development miserable,
but it logs a warning at startup and reports `privilege_separation: false` in
`memory_stats`. The posture is visible rather than assumed — in deployment
(Phase 04.4) the Warden becomes a separate Lambda behind an IAM boundary,
which is the stronger form of the same idea.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psycopg
from mnemos_engine.crypto import Envelope, LocalKeyWrapper
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder, FakeEmbedder, OpenAIEmbedder
from mnemos_engine.engine import MnemosEngine
from mnemos_warden.guarantees import assert_no_model_loaded
from mnemos_warden.keys import KeyProvider, LocalKeyProvider
from mnemos_warden.warden import Warden

from .config import Settings, get_settings

if TYPE_CHECKING:  # boto3-stubs is dev-only — see mnemos_warden.keys
    from mypy_boto3_s3 import S3Client

log = logging.getLogger("mnemos.api.runtime")


#: Tables whose deletion the API role must not be able to perform. Facts and
#: episodes are the memory itself; audit_chain is the record of what happened
#: to it. A role that can delete any of the three can erase its own tracks.
_NO_DELETE_TABLES = ("mnemos.semantic_facts", "mnemos.episodic_events", "mnemos.audit_chain")


@dataclass(frozen=True)
class DbPosture:
    """What the database says about the connection, measured at startup."""

    api_user: str
    warden_user: str
    api_can_delete: bool
    warden_can_delete: bool

    @property
    def enforced(self) -> bool:
        """True only when the cluster itself would refuse an API-side DELETE.

        Distinct from `Settings.privilege_separation_active`, which only knows
        that two DSN strings differ — two URLs can differ in password alone and
        still grant identical privileges.
        """
        return not self.api_can_delete and self.warden_can_delete


@dataclass
class Runtime:
    settings: Settings
    db: Database
    warden_db: Database
    engine: MnemosEngine
    warden: Warden
    embedder: Embedder
    key_provider: KeyProvider
    db_posture: DbPosture | None = None
    s3: S3Client | None = None
    """Set only when `MNEMOS_S3_ANCHOR_BUCKET` is configured. Used solely to
    presign a read-only, time-limited URL onto an already-anchored checkpoint
    for `explain()`'s deposition — never to write. The API Lambda's IAM role
    grants exactly `s3:GetObject`/`s3:GetObjectRetention`/`s3:ListBucket` on
    that one bucket (infra/api/exec-policy.json's `ReadLedgerAnchorsOnly`
    statement), so a presigned URL this client signs can never authorize more
    than a caller with the raw `s3://` URI and no credentials could already
    ask a judge to fetch by hand — it only saves them the trip through the
    AWS CLI.
    """

    def describe_posture(self) -> dict[str, object]:
        """Configured posture, corrected by what the database actually grants.

        `privilege_separation` is reported as the measured fact where one is
        available, because the claim being made to a reader is "the API cannot
        delete", and only the cluster can settle that.
        """
        posture = self.settings.describe_posture()
        if self.db_posture is not None:
            posture["privilege_separation"] = self.db_posture.enforced
            posture["privilege_separation_source"] = "measured"
            posture["db_user"] = self.db_posture.api_user
            posture["api_can_delete"] = self.db_posture.api_can_delete
            posture["warden_can_delete"] = self.db_posture.warden_can_delete
        else:
            posture["privilege_separation_source"] = "configured"
        return posture

    async def close(self) -> None:
        await self.db.close()
        if self.warden_db is not self.db:
            await self.warden_db.close()


def _build_embedder(settings: Settings) -> Embedder:
    """Real embeddings when a key is present, deterministic fakes otherwise.

    Falling back to FakeEmbedder rather than failing lets the whole test suite
    and the local demos run with no API key and no network — but it is a
    genuinely different system, so it is logged rather than silent.
    """
    if settings.model_provider == "openai" and settings.openai_api_key:
        log.info(
            "embeddings: OpenAI %s @ %d dimensions",
            settings.embed_model,
            settings.embed_dimensions,
        )
        return OpenAIEmbedder(
            api_key=settings.openai_api_key,
            model=settings.embed_model,
            dimensions=settings.embed_dimensions,
        )

    log.warning(
        "embeddings: FakeEmbedder (deterministic, NOT semantic) — no %s key configured. "
        "Retrieval quality is meaningless in this mode; plumbing still works.",
        settings.model_provider,
    )
    return FakeEmbedder(dimension=settings.embed_dimensions)


async def _probe_role(db: Database) -> tuple[str, bool]:
    """Ask the cluster who we are and whether we may DELETE.

    `has_table_privilege` rather than an attempted-and-rolled-back DELETE: it
    answers the same question without writing an intent, and it cannot be
    mistaken for real traffic in the audit chain.
    """

    async def run(cur: psycopg.AsyncCursor) -> tuple[str, bool]:
        await cur.execute("SELECT current_user")
        row = await cur.fetchone()
        user = str(row[0]) if row else "unknown"
        can_delete = False
        for table in _NO_DELETE_TABLES:
            await cur.execute("SELECT has_table_privilege(%s, 'DELETE')", (table,))
            granted = await cur.fetchone()
            can_delete = can_delete or bool(granted and granted[0])
        return user, can_delete

    return await db.transaction(None, run, label="probe_role", read_only=True)


async def _probe_posture(db: Database, warden_db: Database) -> DbPosture | None:
    """Best-effort. A probe that could take the service down at startup would
    be a worse failure than not knowing, so an error downgrades the report to
    `privilege_separation_source: configured` rather than raising."""
    try:
        api_user, api_can_delete = await _probe_role(db)
        if warden_db is db:
            warden_user, warden_can_delete = api_user, api_can_delete
        else:
            warden_user, warden_can_delete = await _probe_role(warden_db)
    except Exception:
        log.warning("could not probe database privileges; posture stays configured", exc_info=True)
        return None

    posture = DbPosture(
        api_user=api_user,
        warden_user=warden_user,
        api_can_delete=api_can_delete,
        warden_can_delete=warden_can_delete,
    )
    if posture.enforced:
        log.info(
            "privilege separation ENFORCED by the cluster: %s cannot DELETE, %s can",
            api_user,
            warden_user,
        )
    elif api_can_delete:
        log.warning(
            "database user %r HOLDS DELETE on memory tables. Invariant 1 is not being "
            "enforced by the cluster for this deployment — it rests on application code "
            "alone. Point MNEMOS_DB_URL at a login granted the mnemos_api role.",
            api_user,
        )
    else:
        log.warning(
            "database user %r cannot DELETE, but neither can the Warden user %r — "
            "erasure requests will fail.",
            api_user,
            warden_user,
        )
    return posture


async def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()

    db = Database(settings.database_url, min_size=1, max_size=settings.db_pool_max)
    await db.open()

    if settings.privilege_separation_active:
        assert settings.warden_database_url is not None
        warden_db = Database(
            settings.warden_database_url, min_size=1, max_size=max(2, settings.db_pool_max // 2)
        )
        await warden_db.open()
        log.info("privilege separation ACTIVE: Warden uses a separate database role")
    else:
        warden_db = db
        log.warning(
            "privilege separation INACTIVE: the Warden shares the API's database "
            "connection. Set MNEMOS_DB_URL_WARDEN to a role that holds DELETE while "
            "MNEMOS_DB_URL points at one that does not. Reported as "
            "privilege_separation=false in memory_stats."
        )

    embedder = _build_embedder(settings)
    key_provider = LocalKeyProvider()

    engine = MnemosEngine(
        db,
        embedder=embedder,
        envelope=Envelope(LocalKeyWrapper()),
        actor="api",
        region=settings.region,
    )
    warden = Warden(warden_db, key_provider=key_provider)

    # The third of the no-model guarantee's three checks (the other two are
    # `make no-model-in-warden`'s static scan, at commit time, and the
    # deployed IAM role's Bedrock deny, at the AWS boundary). This one runs
    # here, in-process, at the moment the Warden object actually exists —
    # not merely "before Warden code runs" but "before this Runtime is handed
    # back to anything that could route a request to it".
    #
    # Its blind spot, stated rather than hidden: it inspects `sys.modules`
    # for an imported LLM SDK, and this process's own OpenAIEmbedder/
    # OpenAIChatClient (packages/engine) are deliberately hand-rolled over
    # `urllib` rather than the `openai` package specifically so that
    # dependency stays out of the Warden's own import graph — which also
    # means this check cannot see a raw HTTP call to a model endpoint made by
    # code elsewhere in the SAME process. It catches an SDK creeping into the
    # dependency tree; it does not by itself prove OS-level process isolation,
    # which today does not exist (see docs/security.md's honest limits
    # section) — the Warden's own methods never call the engine's model
    # clients, checked and enforced at the code-path level, not the process
    # level.
    assert_no_model_loaded()

    db_posture = await _probe_posture(db, warden_db)

    s3: S3Client | None = None
    if settings.anchor_bucket:
        import boto3

        s3 = boto3.client("s3", region_name=settings.region)

    return Runtime(
        settings=settings,
        db=db,
        warden_db=warden_db,
        engine=engine,
        warden=warden,
        embedder=embedder,
        key_provider=key_provider,
        db_posture=db_posture,
        s3=s3,
    )
