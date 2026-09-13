"""Phase 6A.7 (corrected by the 6A.7 final corrective pass, §1-§3):
strict, stable `MAJOR.MINOR.PATCH` validation for `SkillDefinition
.version`, followed by deterministic ordering.

The 6A.7 architecture requires a deliberately NARROWER methodology-
version contract than full PEP 440: stable `MAJOR.MINOR.PATCH` only --
no pre-release, no build metadata, no post/dev release segment, no
epoch, no `v`-prefix, no leading zeros, no two-component form.
`STRICT_STABLE_VERSION_PATTERN` is the actual enforcement boundary and
is checked FIRST, before anything else runs.

`packaging.version.Version` is used ONLY AFTER that strict check passes,
and ONLY for deterministic ordering (`1.9.0` vs. `1.10.0`, correctly,
unlike naive lexical string comparison). `packaging.Version` on its own
accepts the full, much broader PEP 440 grammar -- it does NOT itself
enforce this milestone's stable-SemVer contract, and must never be
treated as doing so.

`skill_schema_version` (the CONTRACT's own structure version,
`contracts.py`) is a SEPARATE, unrelated concern -- never parsed or
compared here.
"""
from __future__ import annotations

import re

from packaging.version import Version

__all__ = ["parse_skill_version", "InvalidSkillVersion", "STRICT_STABLE_VERSION_PATTERN"]

STRICT_STABLE_VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
"""Exactly three numeric components (`MAJOR.MINOR.PATCH`), each either a
bare `0` or a digit string with no leading zero. No `v`-prefix, no
pre-release (`-alpha`, `rc1`), no build metadata (`+local`), no post/dev
release segment (`.post1`, `.dev1`), no epoch (`1!2.0.0`), no two- or
four-component form. This is the actual enforcement boundary -- not
`packaging.version.Version`, which is far more permissive on its own."""


class InvalidSkillVersion(ValueError):
    """Raised when a `SkillDefinition.version` string is not a strict,
    stable `MAJOR.MINOR.PATCH` version -- fails closed, never silently
    coerced, normalized, or compared lexically."""


def parse_skill_version(version: str) -> Version:
    """Validate `version` against `STRICT_STABLE_VERSION_PATTERN` FIRST
    (the real enforcement step) and raise `InvalidSkillVersion` on any
    non-conforming string -- including every valid-but-out-of-scope PEP
    440 form (two-component, `v`-prefixed, leading-zero, pre-release,
    post-release, dev-release, epoch, local-version). Only once that
    strict check passes is a `packaging.version.Version` constructed and
    returned, purely for deterministic, correct ordering. A string that
    passes the strict check always parses successfully as a `Version`
    (it is a strict subset of PEP 440's release-segment grammar), so no
    further error handling is needed at this second step."""
    if not STRICT_STABLE_VERSION_PATTERN.match(version):
        raise InvalidSkillVersion(
            f"invalid Skill methodology version: {version!r} -- must be a "
            "stable MAJOR.MINOR.PATCH version with no leading zeros, "
            "v-prefix, prerelease segment, build metadata, epoch, or "
            "post/dev release segment (e.g. '1.0.0', '1.10.0', '25.3.7')"
        )
    return Version(version)
