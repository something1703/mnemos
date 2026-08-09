"""Loads the vendored Agent Skills (`vendor/cockroachdb-skills/`) into a
structured form the sweep loop can use as interpretation-prompt context.

Every skill is a `SKILL.md` (YAML frontmatter + a markdown body) plus a
`references/` directory of supporting docs (`docs/attribution.md` has the
provenance). This module only reads and parses — it makes no judgment about
which MCP tools a skill maps to (`allowlist.py` owns that decision, per
ADR-011) and calls nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "cockroachdb-skills" / "skills"

_REQUIRED_FRONTMATTER_FIELDS = ("name", "description", "compatibility")


class SkillLoadError(Exception):
    """A vendored skill's SKILL.md is missing, malformed, or duplicated.

    Always a packaging bug — vendored content is pinned and reviewed, not
    generated at runtime — so callers should let this propagate rather than
    route around it.
    """


@dataclass(frozen=True)
class Skill:
    skill_id: str
    """The frontmatter `name` field — e.g. `triaging-live-sql-activity`.
    `allowlist.py` keys its MCP tool mapping on this exact string."""

    description: str
    compatibility: str
    version: str
    body: str
    """The markdown body (everything after the frontmatter) — handed to the
    interpretation model as the skill's own triage guidance (PHASE_07 7.3)."""

    references: dict[str, str] = field(default_factory=dict)
    """Relative path within `references/` (e.g. `permissions.md`) → file
    content. Included in interpretation-prompt context alongside `body`."""

    source_path: Path = field(repr=False, compare=False, default=Path())


def _parse_frontmatter(text: str, *, source: Path) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md's `---`-delimited YAML frontmatter from its body."""
    if not text.startswith("---\n"):
        raise SkillLoadError(f"{source}: missing YAML frontmatter (must start with '---')")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise SkillLoadError(f"{source}: unterminated YAML frontmatter (no closing '---')")

    try:
        frontmatter = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"{source}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillLoadError(f"{source}: frontmatter must be a YAML mapping")

    body = text[end + 5 :].lstrip("\n")
    return frontmatter, body


def _load_one(skill_dir: Path) -> Skill:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillLoadError(f"{skill_dir}: no SKILL.md")

    frontmatter, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"), source=skill_md)

    missing = [f for f in _REQUIRED_FRONTMATTER_FIELDS if f not in frontmatter]
    if missing:
        raise SkillLoadError(f"{skill_md}: frontmatter missing required field(s) {missing}")

    metadata = frontmatter.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise SkillLoadError(f"{skill_md}: frontmatter 'metadata' must be a mapping")

    references: dict[str, str] = {}
    ref_dir = skill_dir / "references"
    if ref_dir.is_dir():
        for path in sorted(ref_dir.rglob("*.md")):
            references[str(path.relative_to(ref_dir))] = path.read_text(encoding="utf-8")

    return Skill(
        skill_id=str(frontmatter["name"]),
        description=str(frontmatter["description"]),
        compatibility=str(frontmatter["compatibility"]),
        version=str(metadata.get("version", "unknown")),
        body=body,
        references=references,
        source_path=skill_dir,
    )


def load_all(vendor_root: Path = VENDOR_ROOT) -> dict[str, Skill]:
    """Every vendored skill, keyed by `skill_id`.

    Enumerates by walking for `SKILL.md` files rather than reading a
    hardcoded name list, so a skill directory added to `vendor/` without a
    matching entry in `allowlist.py` is caught by
    `tests/custodian/test_allowlist.py` (which checks the two against each
    other), not silently ignored at runtime.
    """
    if not vendor_root.is_dir():
        raise SkillLoadError(f"vendor root not found: {vendor_root}")

    skills: dict[str, Skill] = {}
    for skill_md in sorted(vendor_root.rglob("SKILL.md")):
        skill = _load_one(skill_md.parent)
        if skill.skill_id in skills:
            raise SkillLoadError(
                f"duplicate skill_id {skill.skill_id!r}: "
                f"{skills[skill.skill_id].source_path} and {skill.source_path}"
            )
        skills[skill.skill_id] = skill
    return skills


__all__ = ["Skill", "SkillLoadError", "load_all"]
