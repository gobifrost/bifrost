"""
Guard: Codex skill mirrors must be in sync with the canonical .claude/skills/ source.

Runs `scripts/sync-codex-skills.sh` and asserts the two mirror roots
(`plugins/bifrost/skills/` and `.codex/skills/`) are byte-identical before and
after — i.e. the committed mirrors already match what the script produces. If
they differ, the mirrors are stale and the script must be re-run + committed.

Git-independent on purpose: the test-runner container has no usable `.git`, so
repo root is derived from this file's path and equality is checked by hashing
the mirror trees rather than `git diff`. The script only READS `.claude/skills/`
and WRITES the two mirror dirs, both writable in-container and on the host.
"""

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]  # repo root on host; / in container
_MIRRORS = ("plugins/bifrost/skills", ".codex/skills")
_SYNC_SCRIPT = _REPO / "scripts" / "sync-codex-skills.sh"

# The test-runner container mounts only api/ and .claude/skills/ — the repo-root
# scripts/, plugins/, and .codex/ trees aren't present there. This is a pure
# repo-hygiene check (filesystem + bash, no stack), so it runs on the host. In
# environments where those paths aren't mounted it skips; CI also enforces the
# same invariant directly as a shell `diff` step (Gate 3 of skill-accuracy).
_paths_available = _SYNC_SCRIPT.exists() and all((_REPO / m).exists() for m in _MIRRORS)
pytestmark = pytest.mark.skipif(
    not _paths_available,
    reason="repo-root scripts/ + mirror dirs not mounted (e.g. test-runner container); CI enforces via shell diff",
)


def _tree_digest(root: Path) -> str:
    """Stable hash of every file path + content under root (sorted)."""
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            h.update(str(path.relative_to(root)).encode())
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def test_codex_mirrors_in_sync() -> None:
    sync_script = _REPO / "scripts" / "sync-codex-skills.sh"
    assert sync_script.exists(), f"sync script not found: {sync_script}"
    assert sync_script.stat().st_mode & 0o111, "sync script is not executable"

    before = {m: _tree_digest(_REPO / m) for m in _MIRRORS}

    run_result = subprocess.run(
        ["bash", str(sync_script)],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
    )
    assert run_result.returncode == 0, (
        f"sync script failed (rc={run_result.returncode}):\n"
        f"stdout: {run_result.stdout}\nstderr: {run_result.stderr}"
    )

    after = {m: _tree_digest(_REPO / m) for m in _MIRRORS}
    stale = [m for m in _MIRRORS if before[m] != after[m]]
    assert not stale, (
        "Codex skill mirrors are out of sync with .claude/skills/: "
        f"{', '.join(stale)}.\nRun `scripts/sync-codex-skills.sh` and commit the result."
    )


def test_public_plugin_skills_do_not_repeat_plugin_namespace() -> None:
    """Public Bifrost plugin skills should be named by action only.

    Codex renders plugin display name + skill name. If a bundled skill's
    frontmatter is already ``bifrost:*``, the UI becomes
    ``Bifrost: Bifrost: ...``.
    """
    public_root = _REPO / "plugins/bifrost/skills"
    skill_files = sorted(public_root.glob("*/SKILL.md"))
    assert skill_files, f"no public plugin skills found under {public_root}"
    for skill_file in skill_files:
        text = skill_file.read_text(encoding="utf-8")
        match = re.search(r"^name:\s*(\S+)\s*$", text, flags=re.MULTILINE)
        assert match is not None, f"{skill_file} has no name frontmatter"
        assert not match.group(1).startswith("bifrost:"), (
            f"{skill_file} repeats the plugin namespace in skill name "
            f"{match.group(1)!r}"
        )
