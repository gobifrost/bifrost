"""
Soft staleness check for the bifrost-build skill's curated reference docs.

Two tests:

1. test_manifest_covers_all_reference_files — always runs (no git needed).
   Loads references/sources.yaml and asserts every references/*.md file has
   a manifest entry and every manifest entry points to a file that exists.

2. test_staleness_is_reported_not_fatal — skipped when git isn't usable
   (e.g. inside the test-runner container where .git is not mounted).
   For each manifest entry, runs `git log --oneline <sha>..HEAD -- <globs>`;
   if any source changed since verified_at_sha it PRINTS an informational
   warning but NEVER asserts-fails. This is a soft gate — stale references
   are expected during active development; the warning prompts a human to
   re-read the source and bump the sha.

Pattern copied from api/tests/unit/test_codex_mirror_sync.py: derive repo
root from __file__ (parents[3]) and use a module-level pytest.mark.skipif
that skips the git-dependent test when .git / git isn't usable in-container.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Repo + manifest paths (filesystem-only, no git required)
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]  # api/tests/unit → repo root
_SKILL_ROOT = _REPO / ".claude" / "skills" / "bifrost-build"
_MANIFEST = _SKILL_ROOT / "references" / "sources.yaml"
_REFS_DIR = _SKILL_ROOT / "references"

# ---------------------------------------------------------------------------
# Skip guard for git-dependent test
# The test-runner container has no usable .git (worktree .git file points
# outside the mount). Mirror the exact pattern from test_codex_mirror_sync.py.
# ---------------------------------------------------------------------------

def _git_usable() -> bool:
    """Return True when we can run `git log` against the repo root."""
    try:
        result = subprocess.run(
            ["git", "-C", str(_REPO), "log", "-1", "--oneline"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_GIT_AVAILABLE = _git_usable()

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _load_manifest() -> list[dict]:
    """Load references/sources.yaml and return the list under 'references'."""
    assert _MANIFEST.exists(), f"sources.yaml not found: {_MANIFEST}"
    with _MANIFEST.open() as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict) and "references" in data, (
        "sources.yaml must have a top-level 'references' key"
    )
    return data["references"]


# ---------------------------------------------------------------------------
# Test 1: manifest ↔ filesystem coverage (always runs)
# ---------------------------------------------------------------------------

def test_manifest_covers_all_reference_files() -> None:
    """Every references/*.md on disk has a manifest entry, and vice-versa."""
    entries = _load_manifest()
    manifest_files = {e["file"] for e in entries}

    # Files on disk (relative to skill root)
    disk_files = {
        f"references/{p.name}"
        for p in _REFS_DIR.glob("*.md")
    }

    missing_from_manifest = disk_files - manifest_files
    assert not missing_from_manifest, (
        "Reference files on disk but missing from sources.yaml:\n  "
        + "\n  ".join(sorted(missing_from_manifest))
        + "\nAdd an entry to .claude/skills/bifrost-build/references/sources.yaml."
    )

    extra_in_manifest = manifest_files - disk_files
    assert not extra_in_manifest, (
        "sources.yaml entries that don't correspond to a file on disk:\n  "
        + "\n  ".join(sorted(extra_in_manifest))
        + "\nRemove the stale entry or create the missing file."
    )

    # Spot-check: each entry has required keys
    for entry in entries:
        for key in ("file", "source_globs", "verified_at_sha"):
            assert key in entry, (
                f"Entry for {entry.get('file', '?')} is missing key '{key}'"
            )


# ---------------------------------------------------------------------------
# Test 2: staleness check (skipped in-container when git isn't usable)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _GIT_AVAILABLE,
    reason=(
        "git not usable (test-runner container has no .git mount); "
        "run this test on the host to check for stale references"
    ),
)
def test_staleness_is_reported_not_fatal() -> None:
    """
    For each manifest entry, check whether any source glob has commits newer
    than verified_at_sha. If stale, print an informational warning.

    This test NEVER asserts-fails on staleness — it's a soft gate.
    The intent is to surface drift during a host-side `pytest` run without
    blocking CI or the in-container `./test.sh unit` run.
    """
    entries = _load_manifest()
    stale: list[str] = []

    for entry in entries:
        ref_file = entry["file"]
        sha = entry["verified_at_sha"]
        globs = entry["source_globs"]

        # git log <sha>..HEAD -- <glob1> <glob2> ...
        cmd = ["git", "-C", str(_REPO), "log", "--oneline", f"{sha}..HEAD", "--"] + globs
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            # git error (e.g. unknown sha) — warn but don't fail
            print(
                f"[WARN] Could not check staleness for {ref_file} "
                f"(git exited {result.returncode}): {result.stderr.strip()}"
            )
            continue

        commits = result.stdout.strip()
        if commits:
            stale.append(ref_file)
            print(
                f"\n[STALE] {ref_file} — source(s) changed since {sha[:8]}:\n"
                f"  globs: {globs}\n"
                f"  commits:\n"
                + "\n".join(f"    {line}" for line in commits.splitlines())
                + "\n  Action: re-read the changed sources, fix the prose, "
                "and bump verified_at_sha in references/sources.yaml."
            )

    if stale:
        print(
            f"\n[SUMMARY] {len(stale)} reference file(s) may be stale: "
            + ", ".join(stale)
            + "\n(This is a soft warning — re-verify and bump verified_at_sha to clear it.)"
        )
    else:
        print("\n[OK] All reference files are current (no source changes since verified_at_sha).")

    # Soft gate: never fail.
    assert True
