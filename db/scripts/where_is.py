"""Where does a subject's memory physically live?

Point at a patient record and see which jurisdiction the bytes are sitting in.
This is a video moment as much as a tool: residency is invisible in every other
agent memory system, and the whole of pillar I is the claim that here it is not.

    uv run python db/scripts/where_is.py --tenant clinic patient:eu:8f2c

On the 9-node rig (after promote_regional_by_row.py) `crdb_region` is the real
partition and the answer is physical. On single-region Cloud Basic the answer
comes from `home_region`, which the Warden enforces — the same policy, a weaker
guarantee, and the output says which one you are looking at.
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import psycopg
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("psycopg is not installed. Run: uv sync --all-packages --group dev")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject_key")
    parser.add_argument("--tenant", default="clinic", help="tenant slug")
    parser.add_argument("--url", default=os.environ.get("MNEMOS_DB_URL"))
    args = parser.parse_args()

    if not args.url or args.url.startswith("postgresql://<"):
        print("No database URL. Set MNEMOS_DB_URL or pass --url.", file=sys.stderr)
        return 2

    with psycopg.connect(args.url, autocommit=True, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id FROM mnemos.tenants WHERE slug = %s", (args.tenant,))
            row = cur.fetchone()
            if not row:
                print(f"no tenant with slug {args.tenant!r}", file=sys.stderr)
                return 1
            tenant_id = row[0]
            cur.execute(f"SET app.tenant_id = '{tenant_id}'")

            cur.execute("SELECT count(*) FROM [SHOW REGIONS FROM DATABASE]")
            region_row = cur.fetchone()
            multi_region = bool(region_row and region_row[0] > 1)

            cur.execute(
                "SELECT home_region, count(*) FROM mnemos.episodic_events "
                "WHERE tenant_id = %s AND subject_key = %s GROUP BY home_region",
                (tenant_id, args.subject_key),
            )
            episodes = cur.fetchall()

            cur.execute(
                "SELECT home_region, count(*) FROM mnemos.semantic_facts "
                "WHERE tenant_id = %s AND subject_key = %s GROUP BY home_region",
                (tenant_id, args.subject_key),
            )
            facts = cur.fetchall()

            cur.execute(
                "SELECT subject_pattern, home_region, projection "
                "FROM mnemos.residency_policies WHERE tenant_id = %s ORDER BY priority",
                (tenant_id,),
            )
            policies = cur.fetchall()

    print(f"\nsubject: {args.subject_key}   tenant: {args.tenant}")
    print(
        "guarantee: "
        + (
            "REGIONAL BY ROW — rows are physically partitioned by region"
            if multi_region
            else "Warden-enforced — single-region cluster, home_region is a policy column"
        )
    )

    if not episodes and not facts:
        print("\n  no memory for this subject")
        return 0

    print("\n  episodes:")
    for region, count in episodes:
        print(f"    {region:<16} {count}")
    print("  facts:")
    for region, count in facts:
        print(f"    {region:<16} {count}")

    matching = [p for p in policies if _matches(args.subject_key, p[0])]
    if matching:
        pattern, region, projection = matching[0]
        print(f"\n  governing policy: {pattern} -> home {region}, may cross as: {projection}")
    else:
        print("\n  governing policy: none matched; tenant default applies")
    return 0


def _matches(subject_key: str, pattern: str) -> bool:
    if pattern.endswith("*"):
        return subject_key.startswith(pattern[:-1])
    return subject_key == pattern


if __name__ == "__main__":
    raise SystemExit(main())
