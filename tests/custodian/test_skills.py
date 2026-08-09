"""The skill loader against the real vendored skills — not a fixture stand-in.
A schema drift in the pinned commit (a renamed frontmatter field, a missing
SKILL.md) should fail here, in CI, not silently at Custodian startup."""

from __future__ import annotations

from pathlib import Path

import pytest
from mnemos_custodian.skills import VENDOR_ROOT, Skill, SkillLoadError, _load_one, load_all

EXPECTED_SKILL_IDS = {
    "triaging-live-sql-activity",
    "profiling-statement-fingerprints",
    "analyzing-range-distribution",
    "reviewing-cluster-health",
    "cockroachdb-sql",
}


def test_load_all_enumerates_every_vendored_skill() -> None:
    skills = load_all()
    assert set(skills) == EXPECTED_SKILL_IDS


def test_every_skill_has_the_required_frontmatter_fields() -> None:
    for skill in load_all().values():
        assert skill.skill_id
        assert skill.description
        assert skill.compatibility
        assert skill.version != ""


def test_every_skill_has_a_non_trivial_body() -> None:
    """The body is what the sweep loop hands the interpretation model as
    triage guidance (PHASE_07 7.3) — an empty body would silently produce a
    Custodian with no actual expertise behind its findings."""
    for skill in load_all().values():
        assert len(skill.body) > 500, f"{skill.skill_id}: suspiciously short body"


def test_every_skill_has_at_least_one_reference_file() -> None:
    for skill in load_all().values():
        assert skill.references, f"{skill.skill_id}: no references/ files loaded"


def test_references_keyed_by_relative_path_not_full_path() -> None:
    skill = load_all()["triaging-live-sql-activity"]
    assert "permissions.md" in skill.references
    assert "sql-queries.md" in skill.references


def test_missing_vendor_root_raises() -> None:
    with pytest.raises(SkillLoadError, match="vendor root not found"):
        load_all(Path("/no/such/directory"))


def test_load_all_on_a_tree_with_no_skill_md_anywhere_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "not-a-skill").mkdir()
    assert load_all(tmp_path) == {}


def test_load_one_on_a_directory_with_no_skill_md_raises(tmp_path: Path) -> None:
    """`_load_one` (unlike `load_all`, which only ever visits directories it
    already found a SKILL.md in via rglob) still guards this directly —
    exercised here since `load_all`'s own discovery can't reach it."""
    skill_dir = tmp_path / "some-skill"
    skill_dir.mkdir()
    with pytest.raises(SkillLoadError, match=r"no SKILL\.md"):
        _load_one(skill_dir)


def test_missing_frontmatter_delimiter_raises(tmp_path: Path) -> None:
    skill_dir = tmp_path / "broken-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Just a heading, no frontmatter\n")
    with pytest.raises(SkillLoadError, match="missing YAML frontmatter"):
        load_all(tmp_path)


def test_unterminated_frontmatter_raises(tmp_path: Path) -> None:
    skill_dir = tmp_path / "broken-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: x\ndescription: y\n")
    with pytest.raises(SkillLoadError, match="unterminated YAML frontmatter"):
        load_all(tmp_path)


def test_missing_required_field_raises(tmp_path: Path) -> None:
    skill_dir = tmp_path / "broken-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\n---\n\nbody without compatibility\n"
    )
    with pytest.raises(SkillLoadError, match="missing required field"):
        load_all(tmp_path)


def test_duplicate_skill_id_raises(tmp_path: Path) -> None:
    body = "---\nname: dup\ndescription: d\ncompatibility: c\n---\n\nbody\n" * 10
    for sub in ("a", "b"):
        skill_dir = tmp_path / sub
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(body)
    with pytest.raises(SkillLoadError, match="duplicate skill_id"):
        load_all(tmp_path)


def test_default_vendor_root_points_inside_the_installed_package() -> None:
    """A path bug here (e.g. one `parents[]` index off) would silently pass
    every other test if VENDOR_ROOT and the test's own root happened to
    coincide, so this checks it directly rather than only indirectly through
    load_all()'s success."""
    assert VENDOR_ROOT.is_dir()
    assert (VENDOR_ROOT / "cockroachdb-observability-and-diagnostics").is_dir()


def test_skill_is_frozen_and_hashable_by_identity() -> None:
    skill = next(iter(load_all().values()))
    assert isinstance(skill, Skill)
    with pytest.raises(Exception):  # noqa: B017 — frozen dataclass raises FrozenInstanceError
        skill.skill_id = "changed"  # type: ignore[misc]
