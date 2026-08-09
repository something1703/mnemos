"""ADR-011's allowlist: which Cloud MCP tools each vendored skill may call.

The Phase 02.1 probe found that Cloud Basic restricts `crdb_internal` and
`system` access, which most of these skills' own diagnostic SQL leans on.
Rather than parse raw SQL out of `references/*.md` and hope it runs, the
Custodian treats the CockroachDB Cloud MCP server's purpose-built tools as
its entire reachable surface: the skills supply the triage *expertise*
(their `SKILL.md` body, handed to the interpretation model as prompt
context), the MCP server supplies the safe *accessors*. A skill's own
diagnostic that has no MCP equivalent is skipped and logged as unavailable
by the sweep loop (PHASE_07 7.1) — never silently dropped.

This module is also where the read-only boundary is drawn structurally: only
tools in `READ_ONLY_TOOLS` may ever appear in an `ALLOWLIST` entry
(`tests/custodian/test_allowlist.py` checks this), and `WRITE_CAPABLE_TOOLS`
is what `mcp_client.py`'s startup assertion hard-fails on if reachable at
all.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The complete read-only Cloud MCP tool catalog this project has observed
#: (checked against the live server's `list_tools()` at startup, not just
#: assumed — see `mcp_client.py`).
READ_ONLY_TOOLS = frozenset(
    {
        "show_running_queries",
        "show_statement",
        "explain_query",
        "get_table_schema",
        "list_tables",
        "list_databases",
        "get_cluster",
        "list_clusters",
    }
)

#: Deliberately never allowlisted for any skill, despite being read-only.
#: `select_query` accepts an arbitrary SELECT string; ADR-011's whole
#: argument for MCP's purpose-built tools over the skills' raw SQL was
#: removing SQL-injection-*shaped* surface entirely, and a free-text SELECT
#: escape hatch undoes that even though the server restricts it to one
#: statement type. Every tool in READ_ONLY_TOOLS above takes structured
#: parameters (a table name, a database name) instead.
EXCLUDED_FREE_TEXT_TOOLS = frozenset({"select_query"})

#: What `mcp_client.py`'s startup assertion refuses to see reachable at all.
WRITE_CAPABLE_TOOLS = frozenset({"create_database", "create_table", "insert_rows"})


@dataclass(frozen=True)
class SkillAllowlist:
    skill_id: str
    mcp_tools: frozenset[str]
    note: str
    """Why these specific tools, and what the skill's own diagnostics this
    does NOT cover — read by a human reviewing the mapping, not by code."""


ALLOWLIST: dict[str, SkillAllowlist] = {
    "triaging-live-sql-activity": SkillAllowlist(
        skill_id="triaging-live-sql-activity",
        mcp_tools=frozenset({"show_running_queries", "show_statement"}),
        note=(
            "show_running_queries is the direct match for the skill's core "
            "'SHOW CLUSTER STATEMENTS' triage query. show_statement covers "
            "its SHOW-family drill-downs (SHOW INDEXES, SHOW CREATE TABLE, "
            "SHOW REGIONS) used to correlate a slow query to schema. The "
            "skill's crdb_internal.cluster_transactions query has no MCP "
            "equivalent and is skipped, not silently omitted."
        ),
    ),
    "profiling-statement-fingerprints": SkillAllowlist(
        skill_id="profiling-statement-fingerprints",
        mcp_tools=frozenset({"show_statement"}),
        note=(
            "The skill's core diagnostics query crdb_internal."
            "statement_statistics directly, which Cloud Basic restricts "
            "(Phase 02.1 probe). show_statement's SHOW-family introspection "
            "is the closest available substitute for schema/index context; "
            "the fingerprint-ranking queries themselves have no MCP "
            "equivalent and are skipped, not silently omitted."
        ),
    ),
    "analyzing-range-distribution": SkillAllowlist(
        skill_id="analyzing-range-distribution",
        mcp_tools=frozenset({"show_statement", "get_table_schema", "list_tables"}),
        note=(
            "SHOW RANGES (the skill's core query) is expressible through "
            "show_statement. get_table_schema/list_tables give the table "
            "inventory the skill iterates over before ranging each one."
        ),
    ),
    "reviewing-cluster-health": SkillAllowlist(
        skill_id="reviewing-cluster-health",
        mcp_tools=frozenset({"get_cluster", "list_databases", "list_tables", "list_clusters"}),
        note=(
            "The skill's own tiering already anticipates degraded access on "
            "Basic ('Basic monitors Request Unit consumption and "
            "connectivity via Cloud Console') — get_cluster is exactly that "
            "shape of fact. list_databases/list_tables/list_clusters cover "
            "its inventory-taking step."
        ),
    ),
    "cockroachdb-sql": SkillAllowlist(
        skill_id="cockroachdb-sql",
        mcp_tools=frozenset({"get_table_schema", "list_tables", "list_databases", "explain_query"}),
        note=(
            "Schema review, not live diagnostics — get_table_schema/"
            "list_tables/list_databases give the schema surface to check "
            "against the skill's anti-pattern rules (references/"
            "cockroachdb-rules/); explain_query checks a specific query's "
            "plan against those rules without executing it."
        ),
    ),
}


def tools_for(skill_id: str) -> frozenset[str]:
    entry = ALLOWLIST.get(skill_id)
    if entry is None:
        raise KeyError(f"no allowlist entry for skill {skill_id!r} — see allowlist.ALLOWLIST")
    return entry.mcp_tools


def is_allowed(skill_id: str, tool_name: str) -> bool:
    """False for an unknown skill_id too — an unmapped skill is allowed
    nothing, not everything."""
    entry = ALLOWLIST.get(skill_id)
    return entry is not None and tool_name in entry.mcp_tools


__all__ = [
    "ALLOWLIST",
    "EXCLUDED_FREE_TEXT_TOOLS",
    "READ_ONLY_TOOLS",
    "WRITE_CAPABLE_TOOLS",
    "SkillAllowlist",
    "is_allowed",
    "tools_for",
]
