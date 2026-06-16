"""
Drift test: every name in PLATFORM_EXPORT_NAMES has a `### <Name>` section
in .claude/skills/bifrost-build/platform-api.md.

The bifrost-build skill's platform-api.md is the model-facing reference for
what `import { X } from "bifrost"` provides. The canonical list of names
lives in `bifrost.platform_names.PLATFORM_EXPORT_NAMES`. If a new export
ships without a matching doc entry, a model generating app code won't know
its signature and will either guess (wrong) or skip it entirely.

This test locks the skill docs to PLATFORM_EXPORT_NAMES, mirroring
`test_platform_names_match_runtime.py` which locks PLATFORM_EXPORT_NAMES
to the client `$` registry. Together they form a one-way chain:

    client `$` registry  ─►  PLATFORM_EXPORT_NAMES  ─►  platform-api.md
    (drift test 1)            (drift test 2 — this file)
"""
from __future__ import annotations

import re
from pathlib import Path

from bifrost.platform_names import PLATFORM_EXPORT_NAMES

# api/tests/unit/test_platform_api_docs.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLATFORM_API_DOC = (
    _REPO_ROOT
    / ".claude"
    / "skills"
    / "bifrost-build"
    / "references"
    / "platform-api.md"
)


def _documented_names(md_src: str) -> set[str]:
    """Return every identifier that appears as a `### <Name>` header.

    Matches lines of the form `### SomeName` (PascalCase or lowercase
    identifier), anchored at the start of a line. Case-sensitive. Trailing
    whitespace and markdown annotations after the name are ignored.
    """
    names: set[str] = set()
    for m in re.finditer(
        r"^###\s+([A-Za-z_][A-Za-z0-9_]*)\s*$",
        md_src,
        re.MULTILINE,
    ):
        names.add(m.group(1))
    return names


def test_every_platform_export_has_a_doc_section() -> None:
    """PLATFORM_EXPORT_NAMES ⊆ names documented as `### <Name>` in platform-api.md."""
    assert _PLATFORM_API_DOC.exists(), (
        f"Missing skill doc file: {_PLATFORM_API_DOC}. "
        f"The bifrost-build skill requires platform-api.md."
    )

    md_src = _PLATFORM_API_DOC.read_text(encoding="utf-8")
    documented = _documented_names(md_src)

    missing = sorted(PLATFORM_EXPORT_NAMES - documented)
    assert not missing, (
        "The following names are in PLATFORM_EXPORT_NAMES but have no "
        f"`### <Name>` section in {_PLATFORM_API_DOC}:\n  "
        + ", ".join(missing)
        + "\nAdd a section for each name. If the export is internal and "
        "intentionally undocumented, remove it from PLATFORM_EXPORT_NAMES "
        "(the client `$` registry drift test will then require removing it "
        "from the runtime as well)."
    )


if __name__ == "__main__":
    md_src = _PLATFORM_API_DOC.read_text(encoding="utf-8")
    documented = _documented_names(md_src)
    missing = PLATFORM_EXPORT_NAMES - documented
    extra = documented - PLATFORM_EXPORT_NAMES
    print(f"PLATFORM_EXPORT_NAMES: {len(PLATFORM_EXPORT_NAMES)}")
    print(f"Documented `### <Name>` sections: {len(documented)}")
    if missing:
        print("MISSING doc sections:", sorted(missing))
    if extra:
        print("Doc sections not in PLATFORM_EXPORT_NAMES:", sorted(extra))
