"""Phase 02.2 — migration runner.

Applies ordered SQL files from db/migrations, recording each in a
`mnemos_migrations` table so re-running is a no-op. Idempotent from zero on
cloud, local, and the 9-node rig (Phase 02.2 acceptance criterion).

Deliberately small and dependency-free rather than pulling in alembic or dbmate:
the schema is raw SQL by design, versions are linear, and a judge reading
db/migrations/*.sql should see exactly what runs — no ORM indirection.

Usage:
    uv run python db/scripts/migrate.py up            # apply pending
    uv run python db/scripts/migrate.py status        # what is applied
    uv run python db/scripts/migrate.py up --url ...  # explicit target
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import psycopg
except ModuleNotFoundError:  # pragma: no cover - dependency guidance
    sys.exit("psycopg is not installed. Run: uv sync --all-packages --group dev")

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS mnemos_migrations (
    version     STRING PRIMARY KEY,
    name        STRING NOT NULL,
    checksum    STRING NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()[:16]


def discover() -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = re.match(r"^(\d{3})_(.+)\.sql$", path.name)
        if not match:
            sys.exit(f"migration filename must be NNN_name.sql: {path.name}")
        found.append(Migration(version=match.group(1), name=match.group(2), path=path))

    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        sys.exit(f"duplicate migration version among {versions}")
    return found


def applied(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM mnemos_migrations")
        return dict(cur.fetchall())


def split_statements(sql: str) -> list[str]:
    """Split on semicolons at statement level.

    Semicolons must be ignored inside three contexts, each of which broke this
    at least once:

      * ``$$``-quoted PL/pgSQL bodies, which are full of statement separators;
      * ``--`` line comments, because prose explaining a migration contains
        ordinary punctuation;
      * single-quoted literals, e.g. a default JSONB value.

    Statements are sent one at a time rather than as one blob because
    CockroachDB rejects some DDL inside a multi-statement transaction.
    """
    statements: list[str] = []
    buf: list[str] = []
    in_dollar = False
    in_line_comment = False
    in_string = False
    i = 0
    while i < len(sql):
        pair = sql[i : i + 2]

        if in_line_comment:
            buf.append(sql[i])
            if sql[i] == "\n":
                in_line_comment = False
            i += 1
            continue

        if not in_dollar and not in_string and pair == "--":
            in_line_comment = True
            buf.append(pair)
            i += 2
            continue

        if not in_string and pair == "$$":
            in_dollar = not in_dollar
            buf.append(pair)
            i += 2
            continue

        char = sql[i]

        if not in_dollar and char == "'":
            in_string = not in_string
            buf.append(char)
            i += 1
            continue

        if char == ";" and not in_dollar and not in_string:
            statement = "".join(buf).strip()
            if statement and not _is_only_comments(statement):
                statements.append(statement)
            buf = []
        else:
            buf.append(char)
        i += 1

    tail = "".join(buf).strip()
    if tail and not _is_only_comments(tail):
        statements.append(tail)
    return statements


def _is_only_comments(statement: str) -> bool:
    return all(not line.strip() or line.strip().startswith("--") for line in statement.splitlines())


def up(conn: psycopg.Connection, *, dry_run: bool = False) -> int:
    with conn.cursor() as cur:
        cur.execute(BOOTSTRAP)

    done = applied(conn)
    pending = [m for m in discover() if m.version not in done]

    for migration in discover():
        recorded = done.get(migration.version)
        if recorded and recorded != migration.checksum:
            sys.exit(
                f"migration {migration.version}_{migration.name} changed after it was applied "
                f"(recorded {recorded}, now {migration.checksum}).\n"
                "Applied migrations are immutable — add a new one instead."
            )

    if not pending:
        print("up to date")
        return 0

    for migration in pending:
        label = f"{migration.version}_{migration.name}"
        if dry_run:
            print(f"[dry-run] would apply {label}")
            continue
        print(f"applying {label} ... ", end="", flush=True)
        statements = split_statements(migration.sql)
        try:
            for statement in statements:
                with conn.cursor() as cur:
                    cur.execute(statement)
        except Exception as exc:
            print("FAILED")
            print(f"\n{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mnemos_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
        print(f"ok ({len(statements)} statements)")
    return 0


def status(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(BOOTSTRAP)
    done = applied(conn)
    for migration in discover():
        mark = "applied" if migration.version in done else "PENDING"
        print(f"  {mark:>7}  {migration.version}_{migration.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["up", "status"])
    parser.add_argument("--url", default=os.environ.get("MNEMOS_DB_URL"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.url or args.url.startswith("postgresql://<"):
        print("No database URL. Set MNEMOS_DB_URL in .env or pass --url.", file=sys.stderr)
        return 2

    with psycopg.connect(args.url, autocommit=True, connect_timeout=15) as conn:
        if args.command == "up":
            return up(conn, dry_run=args.dry_run)
        return status(conn)


if __name__ == "__main__":
    raise SystemExit(main())
