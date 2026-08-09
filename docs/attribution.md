# Attribution

Third-party content vendored into this repository, and why.

---

## `cockroachlabs/cockroachdb-skills`

**What:** Five Agent Skills, vendored whole (their `SKILL.md` plus complete
`references/` directories) into
`services/custodian/src/mnemos_custodian/vendor/cockroachdb-skills/`:

| Skill | Source category |
|---|---|
| `triaging-live-sql-activity` | `cockroachdb-observability-and-diagnostics` |
| `profiling-statement-fingerprints` | `cockroachdb-observability-and-diagnostics` |
| `analyzing-range-distribution` | `cockroachdb-observability-and-diagnostics` |
| `reviewing-cluster-health` | `cockroachdb-operations-and-lifecycle` |
| `cockroachdb-sql` | `cockroachdb-query-and-schema-design` |

**Source:** <https://github.com/cockroachlabs/cockroachdb-skills>, pinned at
commit `e14e86d23ce8ee2e7e40a34ce2944c2502b6eadd` (2026-07-22), matching
PHASE_07_CUSTODIAN.md 7.1's instruction to pin the commit rather than track
`main`. The upstream `LICENSE` (Apache-2.0) and `README.md` (as
`UPSTREAM_README.md`, to avoid colliding with this project's own) are
included alongside the vendored skills for exactly this page's purpose.

**License:** Apache-2.0 — identical to this project's own license
(`LICENSE` at the repo root), so no compatibility question arises. Copyright
notice and license text are preserved unmodified in the vendored copy.

**Why a subset, not the whole repository:** the upstream repo organizes 30+
skills across ten operational categories (onboarding, cost management,
security, resilience, and more); most categories are placeholder directories
for this project's purposes. PHASE_07_CUSTODIAN.md 7.1 names exactly the five
skills above as the Custodian's target set, chosen for what a Cloud Basic
cluster can actually diagnose through the CockroachDB Cloud MCP server's
tool surface (see ADR-011). Vendoring the other 25+ skills would add vendored
surface with no corresponding code path exercising it — the opposite of what
vendoring-and-pinning is meant to buy (a reviewable, bounded diff against a
known-good commit).

**Why vendored at all, rather than a live fetch:** `services/custodian`'s
skill loader (`mnemos_custodian.skills`) reads these files from disk at
process startup. A live fetch from GitHub on every cold start would make the
Custodian's behavior depend on GitHub's availability and on whatever the
`main` branch happens to contain at that moment — neither acceptable for a
component whose findings feed back into semantic memory. Pinning to a commit
SHA, vendored into the image, is what makes a sweep's findings reproducible
against a known skill version.

**Not modified.** Every vendored `SKILL.md` and `references/*.md` file is
byte-identical to the pinned commit. `mnemos_custodian.skills.load_all()`
parses them; nothing in this project edits their content.

**To update the pinned commit:** re-run the vendoring process described in
this file's own git history (a shallow clone at the target commit, copying
the five skill directories plus `LICENSE`/`README.md`), update the commit SHA
and date in the table above, and re-run `tests/custodian/test_skills.py` to
confirm the loader still parses every file — a schema change upstream (a new
required frontmatter field, a renamed skill) would surface there first.

---

## Cloud MCP server tool allowlist (ADR-011)

Not vendored code, but worth recording here for the same reason: the
Custodian's MCP tool allowlist (`mnemos_custodian.allowlist`) is derived from
the CockroachDB Cloud MCP server's own documented tool surface
(`show_running_queries`, `show_statement`, `explain_query`,
`get_table_schema`, `list_tables`, `list_databases`, `get_cluster`) — a
vendor-maintained, purpose-built accessor layer this project depends on but
does not vendor a copy of, since it is a live service rather than static
content.
