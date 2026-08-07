"""Promote the memory tiers to REGIONAL BY ROW on a multi-region cluster.

Kept out of the numbered migrations on purpose. `ALTER TABLE ... SET LOCALITY
REGIONAL BY ROW` requires a multi-region database, and CockroachDB Cloud Basic
is single-region — so a migration containing it could never be idempotent across
both targets, and the schema would fork.

Instead the schema is identical everywhere and carries a plain `home_region`
column that the Warden enforces (invariant 4). On the 9-node rig this script
additionally makes the homing *physical*, so residency stops being a policy the
application honours and becomes a property of where the bytes live.

That difference is stated plainly in docs/limits.md rather than blurred:

    cloud rig  — home_region enforced by the Warden, one physical region
    local rig  — home_region enforced by the Warden AND by REGIONAL BY ROW

Usage:
    uv run python db/scripts/promote_regional_by_row.py --url $MNEMOS_DB_URL_RIG
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import psycopg
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("psycopg is not installed. Run: uv sync --all-packages --group dev")

# Only the tiers that hold subject data. Governance and ledger tables stay
# REGIONAL BY TABLE: an audit chain that fragmented across regions would make
# verification a distributed problem for no benefit.
REGIONAL_TABLES = ["mnemos.episodic_events", "mnemos.semantic_facts"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("MNEMOS_DB_URL_RIG"))
    args = parser.parse_args()

    if not args.url:
        print("No URL. Set MNEMOS_DB_URL_RIG or pass --url.", file=sys.stderr)
        return 2

    with psycopg.connect(args.url, autocommit=True, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM [SHOW REGIONS FROM DATABASE]")
            region_row = cur.fetchone()
            region_count = region_row[0] if region_row else 0

        if region_count < 2:
            print(
                f"database has {region_count} region(s); REGIONAL BY ROW needs a "
                "multi-region database.\n"
                "This is expected on CockroachDB Cloud Basic — home_region stays "
                "enforced by the Warden. Run `make db-multiregion` for the rig."
            )
            return 0

        for table in REGIONAL_TABLES:
            with conn.cursor() as cur:
                # home_region already holds the jurisdiction, so the column
                # becomes the partition key rather than a new one being added.
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN home_region SET NOT NULL")
                cur.execute(f"ALTER TABLE {table} SET LOCALITY REGIONAL BY ROW AS home_region")
            print(f"  {table} -> REGIONAL BY ROW AS home_region")

    print("\nRows are now physically homed. Verify with db/scripts/where_is.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
