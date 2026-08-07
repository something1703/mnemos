"""Phase 02.1 — cluster capability probe.

AGENTS.md: "Verify DDL against the real cluster version on day one of Phase 02.
Do not trust memory or this document over the live cluster."

This script asks the cluster itself what it supports, rather than trusting the
docs or the plan. Every probe runs real DDL/DML in a scratch schema and cleans
up after itself. The result is written to docs/cluster-capabilities.md and is
the input to ADR-006.

Usage:
    uv run python db/scripts/probe.py                  # uses MNEMOS_DB_URL
    uv run python db/scripts/probe.py --url postgresql://...
    uv run python db/scripts/probe.py --strict         # exit 1 if a CRITICAL cap is missing
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

try:
    import psycopg
except ModuleNotFoundError:  # pragma: no cover - dependency guidance
    sys.exit("psycopg is not installed. Run: uv sync --all-packages --group dev")

SCRATCH_SCHEMA = "mnemos_probe"
REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "docs" / "cluster-capabilities.md"


class Criticality(Enum):
    """How much of the plan dies if this capability is absent."""

    CRITICAL = "critical"
    """No workaround exists. The plan changes shape."""

    IMPORTANT = "important"
    """A documented fallback exists; absence forces an ADR."""

    OPTIONAL = "optional"
    """Nice to have; absence costs a feature, not the design."""


@dataclass(frozen=True)
class Probe:
    name: str
    criticality: Criticality
    used_for: str
    """Which phase/feature depends on this. Keep specific — this text lands in
    the report and tells a future reader why we cared."""

    setup: Sequence[str] = field(default_factory=tuple)
    test: Sequence[str] = field(default_factory=tuple)
    fallback: str = ""
    """What we do if this is unsupported. Written before we know the answer, on
    purpose: it forces the design to have an escape hatch."""

    remedy: str = ""
    """If absence is a *disabled setting* rather than a missing feature, the fix.
    The distinction matters: "off by default" and "not implemented" lead to very
    different decisions, and conflating them is how teams abandon a good design."""


@dataclass
class Result:
    probe: Probe
    supported: bool
    detail: str = ""


PROBES: tuple[Probe, ...] = (
    Probe(
        name="VECTOR column type",
        criticality=Criticality.CRITICAL,
        used_for="semantic_facts.embedding — the entire semantic tier (Phase 02.2)",
        test=(f"CREATE TABLE {SCRATCH_SCHEMA}.v (id INT PRIMARY KEY, e VECTOR(1024))",),
        fallback="None. Without native vectors there is no C-SPANN story and no reason "
        "to prefer CockroachDB over Postgres+pgvector. The project premise fails.",
    ),
    Probe(
        name="VECTOR INDEX (C-SPANN)",
        criticality=Criticality.CRITICAL,
        used_for="prefix-scoped ANN recall; tenant isolation inside the index (Phase 02.2)",
        setup=(
            f"CREATE TABLE {SCRATCH_SCHEMA}.vi "
            f"(tenant_id UUID, id INT, e VECTOR(1024), PRIMARY KEY (tenant_id, id))",
        ),
        test=(f"CREATE VECTOR INDEX ON {SCRATCH_SCHEMA}.vi (tenant_id, e)",),
        fallback="Fall back to exact KNN over a filtered scan for the demo dataset and "
        "document the ceiling honestly in docs/limits.md. Weakens the scale story badly.",
        remedy="SET CLUSTER SETTING feature.vector_index.enabled = true",
    ),
    Probe(
        name="TSVECTOR + inverted index",
        criticality=Criticality.IMPORTANT,
        used_for="the lexical half of hybrid recall, fused with vectors via RRF (Phase 03.3)",
        setup=(f"CREATE TABLE {SCRATCH_SCHEMA}.ts (id INT PRIMARY KEY, tsv TSVECTOR)",),
        test=(f"CREATE INVERTED INDEX ON {SCRATCH_SCHEMA}.ts (tsv)",),
        fallback="Vector-only recall; drop RRF fusion. Recall quality drops on exact "
        "identifiers and rare tokens, which matters for clinical and ops content.",
    ),
    Probe(
        name="Row-Level TTL",
        criticality=Criticality.IMPORTANT,
        used_for="episodic decay without an LLM or a cron job (Phase 02.2, 05.5)",
        test=(
            f"CREATE TABLE {SCRATCH_SCHEMA}.ttl (id INT PRIMARY KEY, expire_at TIMESTAMPTZ) "
            f"WITH (ttl_expiration_expression = 'expire_at')",
        ),
        fallback="A scheduled Warden job doing bounded deletes. More moving parts, and "
        "it puts deletion on a code path we would rather keep narrow.",
    ),
    Probe(
        name="Row-Level Security",
        criticality=Criticality.CRITICAL,
        used_for="tenant isolation backstop beneath the API middleware (Phase 02.6)",
        setup=(f"CREATE TABLE {SCRATCH_SCHEMA}.rls (tenant_id UUID, v INT)",),
        test=(
            f"ALTER TABLE {SCRATCH_SCHEMA}.rls ENABLE ROW LEVEL SECURITY",
            f"CREATE POLICY p ON {SCRATCH_SCHEMA}.rls USING "
            f"(tenant_id::TEXT = current_setting('app.tenant_id', true))",
        ),
        fallback="Application-layer scoping only. This removes defense-in-depth and makes "
        "the Phase 10.2 'disable the middleware and prove the DB still holds' test "
        "impossible to pass. Would need a prominent limits.md entry.",
    ),
    Probe(
        name="Triggers",
        criticality=Criticality.IMPORTANT,
        used_for="invariant 2 enforced BY THE DATABASE — reject any mutation lacking "
        "an audit row in the same txn (Phase 02.5)",
        setup=(
            f"CREATE TABLE {SCRATCH_SCHEMA}.trg (id INT PRIMARY KEY)",
            f"""CREATE OR REPLACE FUNCTION {SCRATCH_SCHEMA}.trg_fn() RETURNS TRIGGER AS $$
                BEGIN RETURN NEW; END;
            $$ LANGUAGE PLPGSQL""",
        ),
        test=(
            f"CREATE TRIGGER t BEFORE INSERT ON {SCRATCH_SCHEMA}.trg "
            f"FOR EACH ROW EXECUTE FUNCTION {SCRATCH_SCHEMA}.trg_fn()",
        ),
        fallback="ADR-008: revoke direct DML from every application role and force all "
        "writes through stored procedures that append the audit row themselves. "
        "Equally enforced, more ceremony. Invariant 2 survives either way.",
    ),
    Probe(
        name="PL/pgSQL user-defined functions",
        criticality=Criticality.IMPORTANT,
        used_for="the ADR-008 fallback path, and blast-radius recursive helpers",
        test=(
            f"""CREATE OR REPLACE FUNCTION {SCRATCH_SCHEMA}.f(a INT) RETURNS INT AS $$
                BEGIN RETURN a + 1; END;
            $$ LANGUAGE PLPGSQL""",
        ),
        fallback="Push the logic into the engine's transaction wrapper. Weaker, because "
        "enforcement then lives in code we control rather than in the database.",
    ),
    Probe(
        name="AS OF SYSTEM TIME",
        criticality=Criticality.CRITICAL,
        used_for="temporal recall — recall_as_of(), the whole Accountability pillar "
        "(Phase 03.4, 09.3)",
        # AOST binds to a FROM clause; `SELECT 1 AS OF ...` parses `AS` as a column
        # alias and fails. Use the transaction-scoped form, which is also how the
        # engine will actually issue it (one AOST for a whole recall, not per-query).
        test=("SET TRANSACTION AS OF SYSTEM TIME '-1s'", "SELECT 1"),
        fallback="None that preserves the deposition story. We would have to materialise "
        "our own history tables, which is a different (and much larger) project.",
    ),
    Probe(
        name="Recursive CTE",
        criticality=Criticality.CRITICAL,
        used_for="blast_radius() transitive closure over the provenance graph (Phase 03.6)",
        test=(
            "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 5) "
            "SELECT max(n) FROM r",
        ),
        fallback="Iterative fetch loops in the engine. Correct but far slower, and it "
        "would break the <2s blast-radius target at 100k facts.",
    ),
    Probe(
        name="SELECT ... FOR UPDATE",
        criticality=Criticality.CRITICAL,
        used_for="hash-chain head locking per shard (Phase 02.5, 03.8)",
        setup=(f"CREATE TABLE {SCRATCH_SCHEMA}.fu (id INT PRIMARY KEY)",),
        test=(f"SELECT * FROM {SCRATCH_SCHEMA}.fu WHERE id = 1 FOR UPDATE",),
        fallback="Rely on SERIALIZABLE conflict detection alone and retry harder. Works, "
        "but the 40001 rate under contention gets much worse.",
    ),
    Probe(
        name="Multi-region (SHOW REGIONS)",
        criticality=Criticality.OPTIONAL,
        used_for="REGIONAL BY ROW residency (Phase 02.3, 06.2)",
        test=("SHOW REGIONS",),
        fallback="Expected to be absent on Basic. Residency is demonstrated on the local "
        "9-node rig (make db-multiregion); the cloud cluster stays single-region. "
        "This is already the plan — absence here is not a setback.",
    ),
    Probe(
        name="Zone config (gc.ttlseconds)",
        criticality=Criticality.IMPORTANT,
        used_for="extending the AS OF SYSTEM TIME window for subjects under legal "
        "hold (Phase 06.3)",
        setup=(f"CREATE TABLE {SCRATCH_SCHEMA}.zc (id INT PRIMARY KEY)",),
        test=(f"ALTER TABLE {SCRATCH_SCHEMA}.zc CONFIGURE ZONE USING gc.ttlseconds = 90000",),
        fallback="Legal hold cannot extend the temporal window on this cluster. Hold still "
        "blocks erasure and TTL; but recall_as_of beyond the default GC window "
        "will fail. Must be stated plainly in docs/limits.md — this is exactly "
        "the kind of limit we publish rather than paper over.",
    ),
    Probe(
        name="Changefeed",
        criticality=Criticality.IMPORTANT,
        used_for="the revocation bus and the console's live fact stream (Phase 02.7)",
        setup=(
            f"CREATE TABLE {SCRATCH_SCHEMA}.cf (id INT PRIMARY KEY)",
            f"INSERT INTO {SCRATCH_SCHEMA}.cf VALUES (1)",
        ),
        # A sinkless (EXPERIMENTAL) changefeed streams forever and would block
        # execute() until the statement timeout. A real sink creates a job and
        # returns a job id immediately — which is also how we use CDC in
        # production, so this probes the thing we actually depend on.
        test=(f"CREATE CHANGEFEED FOR TABLE {SCRATCH_SCHEMA}.cf INTO 'null://'",),
        fallback="Poll for revocations on a short interval. Functionally close for the "
        "demo; loses the 'revocation escapes the database' narrative and adds "
        "a propagation-delay caveat.",
        remedy="SET CLUSTER SETTING kv.rangefeed.enabled = true",
    ),
)


def _exec(cur: psycopg.Cursor, statements: Sequence[str]) -> None:
    for stmt in statements:
        cur.execute(stmt)


def run_probe(conn: psycopg.Connection, probe: Probe) -> Result:
    """Run one probe in isolation. Any failure is an answer, not an error."""
    is_changefeed = "CHANGEFEED" in " ".join(probe.test)
    job_id: int | None = None
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                _exec(cur, probe.setup)
                # A changefeed job is not transactional — it survives the rollback
                # below, so capture its id and cancel it explicitly.
                if is_changefeed:
                    cur.execute(probe.test[0])
                    row = cur.fetchone()
                    job_id = int(row[0]) if row else None
                else:
                    _exec(cur, probe.test)
            raise _Rollback
    except _Rollback:
        return Result(probe, supported=True)
    except Exception as exc:
        return Result(probe, supported=False, detail=_first_line(exc))
    finally:
        if job_id is not None:
            # Cancel only the job we created. Never cancel by wildcard: on a shared
            # or cloud cluster that would kill someone else's changefeed.
            with contextlib.suppress(Exception), conn.cursor() as cur:
                cur.execute("CANCEL JOB %s", (job_id,))


class _Rollback(Exception):  # noqa: N818 - a control-flow sentinel, not an error
    """Sentinel: the probe succeeded, so undo whatever it created."""


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__


def cluster_facts(conn: psycopg.Connection) -> dict[str, str]:
    facts: dict[str, str] = {}
    queries = {
        "version": "SELECT version()",
        "current_database": "SELECT current_database()",
        "current_user": "SELECT current_user",
        "cluster_id": "SELECT crdb_internal.cluster_id()::STRING",
        # This number IS the temporal-recall window. recall_as_of() cannot see
        # further back than gc.ttlseconds, so it bounds the Accountability pillar
        # directly. Phase 06.3 extends it per-range for subjects under legal hold.
        "default_gc_ttlseconds": (
            "SELECT raw_config_sql FROM [SHOW ZONE CONFIGURATION FOR RANGE default]"
        ),
    }
    for key, sql in queries.items():
        try:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                facts[key] = " ".join(str(row[0]).split()) if row else "(empty)"
        except Exception as exc:
            facts[key] = f"unavailable — {_first_line(exc)}"

    gc_seconds = _extract_gc_ttl(facts.get("default_gc_ttlseconds", ""))
    if gc_seconds:
        facts["temporal_recall_window"] = (
            f"{gc_seconds}s (~{gc_seconds / 3600:.1f}h) — the hard limit on recall_as_of(); "
            "see Phase 06.3 for extending it under legal hold"
        )
    return facts


def _extract_gc_ttl(config_sql: str) -> int | None:
    match = re.search(r"gc\.ttlseconds\s*=\s*(\d+)", config_sql)
    return int(match.group(1)) if match else None


def render_report(facts: dict[str, str], results: list[Result]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    missing_critical = [
        r for r in results if not r.supported and r.probe.criticality is Criticality.CRITICAL
    ]
    missing_important = [
        r for r in results if not r.supported and r.probe.criticality is Criticality.IMPORTANT
    ]

    lines = [
        "# Cluster capabilities",
        "",
        "> Generated by `make db-probe` (`db/scripts/probe.py`). Do not edit by hand.",
        "> This file is the evidence behind ADR-006. Every capability below was",
        "> verified by executing real DDL against the live cluster, not read from docs.",
        "",
        f"**Probed:** {now}",
        "",
        "## Cluster",
        "",
        "| Property | Value |",
        "|---|---|",
    ]
    lines += [f"| `{k}` | `{v}` |" for k, v in facts.items()]

    lines += [
        "",
        "## Capabilities",
        "",
        "| Capability | Supported | Criticality | Used for |",
        "|---|---|---|---|",
    ]
    for r in results:
        mark = "yes" if r.supported else "**NO**"
        lines.append(
            f"| {r.probe.name} | {mark} | {r.probe.criticality.value} | {r.probe.used_for} |"
        )

    unsupported = [r for r in results if not r.supported]
    if unsupported:
        lines += ["", "## Unavailable — what we do instead", ""]
        for r in unsupported:
            lines += [
                f"### {r.probe.name} ({r.probe.criticality.value})",
                "",
                f"**Cluster said:** `{r.detail}`",
                "",
            ]
            if r.probe.remedy:
                lines += [
                    "**This is a disabled setting, not a missing feature.** Enable with:",
                    "",
                    f"```sql\n{r.probe.remedy};\n```",
                    "",
                    "On CockroachDB Cloud Basic, cluster settings may be restricted — if this "
                    "fails there, open a support ticket before falling back.",
                    "",
                ]
            lines += [f"**Fallback if it truly cannot be enabled:** {r.probe.fallback}", ""]

    blocked = [r for r in missing_critical if not r.probe.remedy]
    fixable = [r for r in unsupported if r.probe.remedy]

    lines += ["", "## Verdict", ""]
    if blocked:
        lines += [
            "**The plan needs rework.** These capabilities have no fallback and no remedy:",
            "",
            *[f"- {r.probe.name}" for r in blocked],
            "",
            "Stop and revise MASTER_PLAN.md before writing schema code.",
        ]
    elif fixable:
        lines += [
            "**Proceed after enabling settings.** Nothing is missing; the following are",
            "merely switched off:",
            "",
            *[f"- {r.probe.name} — `{r.probe.remedy}`" for r in fixable],
            "",
            "Re-run `make db-probe` after enabling and confirm this section empties.",
        ]
    elif missing_important:
        lines += [
            "**Proceed, with ADRs.** Every critical capability is present. These need a",
            "documented deviation in `docs/decisions.md` before the phase that uses them:",
            "",
            *[f"- {r.probe.name} — {r.probe.fallback.split('.')[0]}." for r in missing_important],
        ]
    else:
        lines += ["Every probed capability is available. The plan runs as written."]

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("MNEMOS_DB_URL"))
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 if any CRITICAL capability is missing"
    )
    args = parser.parse_args()

    if not args.url or args.url.startswith("postgresql://<"):
        return _fail(
            "No database URL. Set MNEMOS_DB_URL in .env (see .env.example) or pass --url.\n"
            "The Cloud connection string is available in the CockroachDB Cloud console\n"
            "under Connect -> Connection string."
        )

    try:
        conn = psycopg.connect(args.url, autocommit=True, connect_timeout=15)
    except Exception as exc:
        return _fail(f"Could not connect: {_first_line(exc)}")

    with conn:
        facts = cluster_facts(conn)
        print(f"connected: {facts.get('version', 'unknown')}\n")

        with conn.cursor() as cur:
            # A sinkless changefeed streams forever; without this a probe could hang.
            cur.execute("SET statement_timeout = '15s'")
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCRATCH_SCHEMA}")
        try:
            results = []
            for probe in PROBES:
                result = run_probe(conn, probe)
                results.append(result)
                mark = "  ok  " if result.supported else " MISS "
                print(f"[{mark}] {probe.name}")
                if not result.supported:
                    print(f"          {result.detail}")
        finally:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {SCRATCH_SCHEMA} CASCADE")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(facts, results), encoding="utf-8")
    print(f"\nwrote {REPORT_PATH.relative_to(REPO_ROOT)}")

    fixable = [r for r in results if not r.supported and r.probe.remedy]
    if fixable:
        print("\nDisabled, not missing — enable with:")
        for r in fixable:
            print(f"  {r.probe.remedy};   -- {r.probe.name}")

    blocked = [
        r
        for r in results
        if not r.supported and r.probe.criticality is Criticality.CRITICAL and not r.probe.remedy
    ]
    if blocked:
        print("\nCRITICAL and unremediable:")
        for r in blocked:
            print(f"  - {r.probe.name}: {r.probe.fallback}")
        if args.strict:
            return 1
    return 0


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception:
        traceback.print_exc()
        raise SystemExit(2) from None
