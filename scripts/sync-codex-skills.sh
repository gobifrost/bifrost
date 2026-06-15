#!/usr/bin/env bash
# sync-codex-skills.sh — regenerate both Codex plain-file skill mirrors from
# the canonical .claude/skills/ source.  Run after any skill edit; the pytest
# guard in api/tests/unit/test_codex_mirror_sync.py will fail CI if the
# mirrors drift.
#
# Mirror rules:
#   plugins/bifrost/skills/  = PUBLIC  skills (dirs that have a symlink under skills/)
#   .codex/skills/           = MAINTAINER skills (.claude/skills/* NOT in the public set)
#
# Idempotent: running twice produces no changes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SKILLS_DIR="${REPO_ROOT}/skills"
CANONICAL_DIR="${REPO_ROOT}/.claude/skills"
PUBLIC_MIRROR="${REPO_ROOT}/plugins/bifrost/skills"
MAINTAINER_MIRROR="${REPO_ROOT}/.codex/skills"

# ── 1. Compute the public-target basenames from skills/ symlinks ──────────────
declare -A public_targets   # basename → 1

for link in "${SKILLS_DIR}"/*; do
    [[ -L "${link}" ]] || continue          # only symlinks
    target="$(readlink "${link}")"          # e.g. ../.claude/skills/bifrost-build
    basename_target="$(basename "${target}")"
    public_targets["${basename_target}"]=1
done

# ── 2. PUBLIC mirror (plugins/bifrost/skills/) ────────────────────────────────
mkdir -p "${PUBLIC_MIRROR}"

echo "=== PUBLIC mirror → ${PUBLIC_MIRROR} ==="
for target_name in "${!public_targets[@]}"; do
    src="${CANONICAL_DIR}/${target_name}"
    dst="${PUBLIC_MIRROR}/${target_name}"
    if [[ ! -d "${src}" ]]; then
        # A skills/ symlink pointing at a missing canonical dir would silently
        # drop that public skill from the mirror. Fail hard so CI catches it.
        echo "  ERROR: skills/ symlink target missing: ${src}" >&2
        exit 1
    fi
    rsync -a --delete "${src}/" "${dst}/"
    echo "  synced ${target_name}"
done

# Remove stale dirs in the public mirror that are no longer in the public set
for existing in "${PUBLIC_MIRROR}"/*/; do
    [[ -d "${existing}" ]] || continue
    dir_name="$(basename "${existing}")"
    if [[ -z "${public_targets[${dir_name}]+_}" ]]; then
        echo "  removing stale: ${dir_name}"
        rm -rf "${existing}"
    fi
done

# ── 3. MAINTAINER mirror (.codex/skills/) ─────────────────────────────────────
mkdir -p "${MAINTAINER_MIRROR}"

echo "=== MAINTAINER mirror → ${MAINTAINER_MIRROR} ==="
for src in "${CANONICAL_DIR}"/*/; do
    [[ -d "${src}" ]] || continue
    skill_name="$(basename "${src}")"
    if [[ -n "${public_targets[${skill_name}]+_}" ]]; then
        continue    # public skill — belongs in plugins/bifrost/skills/, not here
    fi
    dst="${MAINTAINER_MIRROR}/${skill_name}"
    rsync -a --delete "${src}/" "${dst}/"
    echo "  synced ${skill_name}"
done

# Remove stale dirs in the maintainer mirror that are no longer maintainer-only
for existing in "${MAINTAINER_MIRROR}"/*/; do
    [[ -d "${existing}" ]] || continue
    dir_name="$(basename "${existing}")"
    src="${CANONICAL_DIR}/${dir_name}"
    # Stale if: canonical source is gone OR the skill moved to the public set
    if [[ ! -d "${src}" ]] || [[ -n "${public_targets[${dir_name}]+_}" ]]; then
        echo "  removing stale: ${dir_name}"
        rm -rf "${existing}"
    fi
done

echo "=== Done ==="
