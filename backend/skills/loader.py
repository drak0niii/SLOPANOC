"""Phase 6A.7: strict, fail-closed declarative Skill-definition loading
(§13/§14/§48).

Production Skill definitions are YAML (`yaml.safe_load` EXCLUSIVELY --
never `yaml.load`, never a custom constructor capable of arbitrary
object instantiation; proven by `backend/tests/skills/test_dependency_
boundary.py`'s own source-scan). `SkillDefinition.model_config =
{"extra": "forbid"}` (`contracts.py`) means any unknown field already
fails closed at the pydantic layer -- this module adds only the
YAML-parsing step and file-level error reporting on top of that.

NO EXECUTABLE CONTENT OF ANY KIND: a Skill definition file is DATA only
-- there is no code path anywhere in this module that evaluates,
imports, or executes anything the file's own content specifies.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from backend.skills.contracts import SkillDefinition

__all__ = ["SkillLoadError", "load_skill_definition_from_yaml_text", "load_skill_definitions_from_directory"]


class SkillLoadError(ValueError):
    """Raised on any malformed/ambiguous/unsafe Skill definition --
    always carries the definition's own source (text snippet or file
    path) so a caller can report exactly which definition failed and
    why (§48's own "must be reported explicitly with its definition
    source/path")."""


def load_skill_definition_from_yaml_text(text: str, *, source: str = "<string>") -> SkillDefinition:
    """Parses `text` with `yaml.safe_load` (never `yaml.load`) and
    strictly validates the result against `SkillDefinition`. Fails
    closed -- never partially coerces unsafe/malformed data into a valid
    Skill (§48)."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"{source}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise SkillLoadError(f"{source}: a Skill definition must be a YAML mapping, got {type(raw).__name__}")

    try:
        return SkillDefinition.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError, or any other parse failure
        raise SkillLoadError(f"{source}: invalid Skill definition: {exc}") from exc


def load_skill_definitions_from_directory(directory: Path) -> list[SkillDefinition]:
    """Loads every `*.yaml`/`*.yml` file in `directory` (non-recursive),
    in a DETERMINISTIC order (sorted by filename -- never filesystem/glob
    iteration order, §46). One malformed file raises `SkillLoadError`
    immediately, naming that exact file -- it never silently skips a bad
    file and continues (§48)."""
    directory = Path(directory)
    paths = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")], key=lambda p: p.name)
    definitions: list[SkillDefinition] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        definitions.append(load_skill_definition_from_yaml_text(text, source=str(path)))
    return definitions
