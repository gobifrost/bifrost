# Build-Skill Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the `bifrost:build` skill into a hub that dispatches on `bifrost.solution.yaml` to a light `solutions.md` vs a `repo.md` entry doc, backed by machine-generated SDK appendices, three CI accuracy gates, a reference-freshness manifest, and a two-track empirical Sonnet validation loop that drives the full web + Python SDK.

**Architecture:** One plugin skill (`.claude/skills/bifrost-build/`) with a thin hub `SKILL.md`, curated `references/*.md` (mode-specific behavior in entry docs, mode-agnostic facts shared once), and committed `generated/*.md` appendices regenerated deterministically from source. Correctness is enforced by gates (appendix-freshness diff, a CLI-claims linter with mode-conditional bans, a Codex-mirror `diff -r`) and *proven* by fresh Sonnet subagents building real artifacts against the debug stack until 3 consecutive clean runs.

**Tech Stack:** Python (Click introspection, pytest), Bifrost CLI/SDK, FastAPI OpenAPI, TypeScript (app-sdk surface dump via `tsc`/node), bash (sync + version scripts), GitHub Actions, Markdown.

**Spec:** `docs/superpowers/specs/2026-06-15-build-skill-rebuild-design.md`

---

## File Structure

**Created:**
- `api/scripts/skill-truth/generate.py` — regenerates all `generated/*.md` from source (CLI walk + Python `inspect` + OpenAPI digest).
- `api/scripts/skill-truth/lint_claims.py` — extracts + validates every `bifrost …` invocation in `skills/**/*.md`; mode-conditional bans.
- `api/scripts/skill-truth/dump-app-sdk-surface.mjs` — dumps `index.v2.ts` export signatures for `generated/web-sdk-surface.md` (colocated with the generator; dependency-free regex parse).
- `scripts/sync-codex-skills.sh` — rsyncs `.claude/skills/bifrost-*` into the two Codex roots.
- `.claude/skills/bifrost-build/references/{solutions,repo,tables,workflows-python,web-sdk-v2,python-sdk,entities,apps,rest-api,mcp-mode}.md` — curated docs.
- `.claude/skills/bifrost-build/references/sources.yaml` — reference-freshness manifest.
- `.claude/skills/bifrost-build/generated/{cli-reference,python-sdk-signatures,web-sdk-surface,openapi-digest}.md` — appendices (committed).
- `api/tests/unit/test_skill_cli_claims.py` — pytest wrapper for the claims linter.
- `api/tests/unit/test_skill_appendix_fresh.py` — pytest wrapper asserting `generated/*` is regenerated-clean.
- `api/tests/unit/test_skill_reference_freshness.py` — soft staleness warn for `sources.yaml`.
- `docs/plans/2026-06-15-build-skill-validation-log.md` — validation-loop evidence.

**Modified:**
- `.claude/skills/bifrost-build/SKILL.md` — rewritten as the thin hub dispatcher.
- `.claude/skills/bifrost-build/{app-patterns,import-patterns,platform-api}.md` — `app-patterns.md` merged into `apps.md` then removed; `import-patterns.md` + `platform-api.md` kept as v1 refs (moved under `references/`).
- `.github/workflows/ci.yml` — add `skill-accuracy` job.
- `CLAUDE.md`, `AGENTS.md` — repoint the `docs/llm.txt` references.
- `skills/migrate` — normalize to a symlink (or document the exception).

**Deleted:**
- `docs/llm.txt` — after salvage into `references/entities.md`.

---

## Conventions for every task

- **Worktree only.** All work in `solutions-success-criteria`. Never run two `./test.sh` concurrently.
- **Tests:** `./test.sh tests/unit/<file>.py::<test> -v` (the `-k` filter must be a separate arg, not inside the path string). JUnit XML at `/tmp/bifrost-<project>/test-results.xml`.
- **No client specifics** in any committed file (public repo).
- **Commit** at the end of each task with the message shown.

---

## Task 0: Ground-truth generator — CLI reference

**Files:**
- Create: `api/scripts/skill-truth/generate.py`
- Create: `.claude/skills/bifrost-build/generated/cli-reference.md`
- Test: `api/tests/unit/test_skill_appendix_fresh.py`

- [ ] **Step 1: Write the failing test (determinism + presence)**

```python
# api/tests/unit/test_skill_appendix_fresh.py
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / ".claude/skills/bifrost-build/generated"


def _run_generator():
    # generate.py writes generated/*.md; --check leaves the tree unchanged and
    # exits 0 only if a fresh regen would produce zero diff.
    return subprocess.run(
        [sys.executable, str(REPO / "api/scripts/skill-truth/generate.py"), "--check"],
        capture_output=True, text=True,
    )


def test_cli_reference_is_fresh():
    result = _run_generator()
    assert result.returncode == 0, (
        f"generated/* is stale — run api/scripts/skill-truth/generate.py.\n{result.stdout}\n{result.stderr}"
    )
    assert (GEN / "cli-reference.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_skill_appendix_fresh.py::test_cli_reference_is_fresh -v`
Expected: FAIL — `generate.py` does not exist (FileNotFoundError / non-zero return).

- [ ] **Step 3: Write the CLI-walk generator**

```python
# api/scripts/skill-truth/generate.py
"""Regenerate .claude/skills/bifrost-build/generated/*.md from source.

Deterministic: sorted iteration, no timestamps. `--check` writes to a temp
location and diffs against the committed files, exiting non-zero on drift.
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import click

REPO = Path(__file__).resolve().parents[2]
GEN_DIR = REPO / ".claude/skills/bifrost-build/generated"


def _walk_group(name: str, group: click.Group, lines: list[str], depth: int = 0) -> None:
    ctx = click.Context(group, info_name=name)
    lines.append(f"{'#' * (depth + 2)} `{name}`\n")
    with redirect_stdout(io.StringIO()) as buf:
        click.echo(group.get_help(ctx))
    lines.append("```\n" + buf.getvalue().rstrip() + "\n```\n")
    for sub_name in sorted(group.commands):
        sub = group.commands[sub_name]
        if isinstance(sub, click.Group):
            _walk_group(f"{name} {sub_name}", sub, lines, depth + 1)
        else:
            sub_ctx = click.Context(sub, info_name=sub_name, parent=ctx)
            lines.append(f"{'#' * (depth + 3)} `{name} {sub_name}`\n")
            lines.append("```\n" + sub.get_help(sub_ctx).rstrip() + "\n```\n")


def gen_cli_reference() -> str:
    from bifrost.commands import ENTITY_GROUPS
    from bifrost.commands.solution import solution_group

    lines: list[str] = ["# CLI Reference (generated — do not edit)\n"]
    lines.append("> Regenerate: `python api/scripts/skill-truth/generate.py`. CI enforces freshness.\n")
    groups = {**ENTITY_GROUPS, "solution": solution_group}
    for name in sorted(groups):
        _walk_group(name, groups[name], lines)
    return "\n".join(lines) + "\n"


GENERATORS = {"cli-reference.md": gen_cli_reference}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    drift = []
    for fname, fn in sorted(GENERATORS.items()):
        new = fn()
        path = GEN_DIR / fname
        old = path.read_text() if path.exists() else None
        if args.check:
            if old != new:
                drift.append(fname)
        else:
            path.write_text(new)
    if args.check and drift:
        print("STALE: " + ", ".join(drift))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Generate the committed appendix**

Run: `cd api && PYTHONPATH=. python ../api/scripts/skill-truth/generate.py`
(Run from `api/` so `bifrost` imports resolve; if `bifrost` is only importable via the package, use the test stack's interpreter. Verify the file landed: `ls -la .claude/skills/bifrost-build/generated/cli-reference.md`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `./test.sh tests/unit/test_skill_appendix_fresh.py::test_cli_reference_is_fresh -v`
Expected: PASS (double-run produces zero diff).

- [ ] **Step 6: Commit**

```bash
git add api/scripts/skill-truth/generate.py .claude/skills/bifrost-build/generated/cli-reference.md api/tests/unit/test_skill_appendix_fresh.py
git commit -m "feat(skill-truth): deterministic CLI-reference generator + freshness test"
```

---

## Task 1: Ground-truth generator — Python SDK signatures + OpenAPI digest + web-SDK surface

**Files:**
- Modify: `api/scripts/skill-truth/generate.py`
- Create: `client/scripts/dump-app-sdk-surface.mjs`
- Create: `.claude/skills/bifrost-build/generated/{python-sdk-signatures,openapi-digest,web-sdk-surface}.md`
- Test: `api/tests/unit/test_skill_appendix_fresh.py` (extend)

- [ ] **Step 1: Extend the test for the three new appendices**

```python
# append to api/tests/unit/test_skill_appendix_fresh.py
import pytest


@pytest.mark.parametrize("fname", [
    "python-sdk-signatures.md",
    "openapi-digest.md",
    "web-sdk-surface.md",
])
def test_appendix_present_and_fresh(fname):
    result = _run_generator()
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert (GEN / fname).exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_skill_appendix_fresh.py -v`
Expected: FAIL — the three files do not exist yet / `--check` reports STALE.

- [ ] **Step 3: Add the Python-SDK + OpenAPI generators (reuse existing introspection)**

```python
# add to api/scripts/skill-truth/generate.py

def gen_python_sdk_signatures() -> str:
    # Reuse the introspection already used by the served llms.txt.
    from src.services.mcp_server.tools import sdk as sdk_tools
    import importlib, inspect

    lines = ["# Python SDK Signatures (generated — do not edit)\n"]
    modules = [
        "tables", "integrations", "config", "files", "agents", "forms",
        "workflows", "executions", "knowledge", "organizations", "roles",
        "users", "ai", "events",
    ]
    for mod_name in sorted(modules):
        mod = importlib.import_module(f"bifrost.{mod_name}")
        # find the primary client class (first public class defined in the module)
        classes = sorted(
            (n for n, o in inspect.getmembers(mod, inspect.isclass)
             if o.__module__ == mod.__name__ and not n.startswith("_"))
        )
        for cls_name in classes:
            cls = getattr(mod, cls_name)
            lines.append(sdk_tools._generate_module_docs(mod_name, cls))
    return "\n".join(lines) + "\n"


def gen_openapi_digest() -> str:
    from src.main import app
    spec = app.openapi()
    lines = ["# OpenAPI Digest (generated — do not edit)\n", "| Method | Path | operationId |", "|---|---|---|"]
    rows = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            rows.append((method.upper(), path, op.get("operationId", "")))
    for m, p, oid in sorted(rows):
        lines.append(f"| {m} | `{p}` | `{oid}` |")
    return "\n".join(lines) + "\n"
```

Register both in `GENERATORS`, and add the web-SDK file by shelling out to the node dumper:

```python
def gen_web_sdk_surface() -> str:
    import subprocess
    out = subprocess.run(
        ["node", str(REPO / "client/scripts/dump-app-sdk-surface.mjs")],
        capture_output=True, text=True, check=True,
    )
    return out.stdout
```

```javascript
// client/scripts/dump-app-sdk-surface.mjs
// Dump the v2 SDK export surface from index.v2.ts deterministically.
import { Project } from "ts-morph";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const entry = path.resolve(here, "../src/lib/app-sdk/index.v2.ts");
const project = new Project({ tsConfigFilePath: path.resolve(here, "../tsconfig.json") });
const src = project.addSourceFileAtPath(entry);

const lines = ["# Web SDK (v2) Surface (generated — do not edit)\n"];
const names = [];
for (const [name, decls] of src.getExportedDeclarations()) {
  names.push({ name, kind: decls[0]?.getKindName() ?? "unknown" });
}
names.sort((a, b) => a.name.localeCompare(b.name));
for (const { name, kind } of names) lines.push(`- \`${name}\` (${kind})`);
process.stdout.write(lines.join("\n") + "\n");
```

> If `ts-morph` is not already a client dev dependency, add it: `cd client && npm install -D ts-morph`. Verify it's deterministic (no timestamps) before committing.

- [ ] **Step 4: Generate the three appendices**

Run (in the test stack's API interpreter so `src` + `bifrost` import): `python api/scripts/skill-truth/generate.py`
Then: `cd client && node scripts/dump-app-sdk-surface.mjs | head` to sanity-check.
Verify all four `generated/*.md` exist.

- [ ] **Step 5: Run test to verify it passes**

Run: `./test.sh tests/unit/test_skill_appendix_fresh.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/scripts/skill-truth/generate.py client/scripts/dump-app-sdk-surface.mjs client/package.json client/package-lock.json .claude/skills/bifrost-build/generated/ api/tests/unit/test_skill_appendix_fresh.py
git commit -m "feat(skill-truth): python-sdk, openapi, web-sdk surface generators"
```

---

## Task 2: Claims linter (red against current skill) + mode-conditional bans

**Files:**
- Create: `api/scripts/skill-truth/lint_claims.py`
- Test: `api/tests/unit/test_skill_cli_claims.py`

- [ ] **Step 1: Write the failing test (must flag the CURRENT skill's bad commands)**

```python
# api/tests/unit/test_skill_cli_claims.py
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/skill-truth"))
import lint_claims  # noqa: E402


def test_flags_banned_global_commands():
    # A solution-context block doing live entity mutation, plus a globally
    # banned command. Both must be reported.
    md = (
        "```bash solution\nbifrost agents create --name x\n```\n"
        "```bash\nbifrost watch\n```\n"
    )
    findings = lint_claims.lint_text(md, filename="references/solutions.md")
    msgs = " ".join(f.message for f in findings)
    assert "watch" in msgs                  # globally banned
    assert "agents create" in msgs          # banned in solution context


def test_allows_entity_mutation_in_repo_context():
    md = "```bash repo\nbifrost agents create --name x\n```\n"
    findings = lint_claims.lint_text(md, filename="references/repo.md")
    assert all("agents create" not in f.message for f in findings)


def test_flags_unknown_flag():
    md = "```bash\nbifrost orgs list --bogus-flag\n```\n"
    findings = lint_claims.lint_text(md, filename="references/repo.md")
    assert any("bogus-flag" in f.message for f in findings)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_skill_cli_claims.py -v`
Expected: FAIL — `lint_claims` module missing.

- [ ] **Step 3: Write the linter**

```python
# api/scripts/skill-truth/lint_claims.py
"""Validate every `bifrost ...` invocation in skill markdown against the real
Click tree. Globally-banned commands always fail; live entity mutation fails
only in solution context (file == solutions.md or a ```bash solution block)."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import click

REPO = Path(__file__).resolve().parents[2]

GLOBAL_BANNED = {"watch", "push", "pull", "sync", "git", "export", "import"}
# Entity groups whose create/update/delete is forbidden in a solution workspace.
SOLUTION_MANAGED_GROUPS = {"agents", "forms", "tables", "configs", "apps", "workflows", "events"}
MUTATING_VERBS = {"create", "update", "delete"}

# Hand-rolled top-level subcommands (not Click groups) — allowlist only.
HANDROLLED = {"login", "logout", "auth", "run", "api", "migrate-imports", "skill", "solution", "deploy"}

FENCE = re.compile(r"```(?P<info>[^\n]*)\n(?P<body>.*?)```", re.DOTALL)
INLINE = re.compile(r"`(bifrost [^`]+)`")


@dataclass
class Finding:
    filename: str
    message: str


def _load_groups() -> dict[str, click.Group]:
    from bifrost.commands import ENTITY_GROUPS
    from bifrost.commands.solution import solution_group
    return {**ENTITY_GROUPS, "solution": solution_group}


def _block_mode(filename: str, info: str) -> str:
    info = (info or "").strip().lower()
    if "solution" in info:
        return "solution"
    if "repo" in info:
        return "repo"
    if "solutions.md" in filename:
        return "solution"
    return "repo"


def _validate_invocation(tokens: list[str], mode: str, filename: str, groups) -> list[Finding]:
    findings: list[Finding] = []
    if not tokens or tokens[0] != "bifrost" or len(tokens) < 2:
        return findings
    sub = tokens[1]
    if sub in GLOBAL_BANNED:
        return [Finding(filename, f"globally-banned command: bifrost {sub}")]
    if sub in SOLUTION_MANAGED_GROUPS and len(tokens) >= 3 and tokens[2] in MUTATING_VERBS:
        if mode == "solution":
            findings.append(Finding(
                filename,
                f"live entity mutation forbidden in solution context: bifrost {sub} {tokens[2]}",
            ))
            return findings
    grp = groups.get(sub)
    if grp is None:
        if sub not in HANDROLLED:
            findings.append(Finding(filename, f"unknown command: bifrost {sub}"))
        return findings  # hand-rolled flag validation is allowlist-only (out of scope here)
    # Click group: validate the verb + its flags.
    if len(tokens) >= 3:
        verb = tokens[2]
        cmd = grp.commands.get(verb)
        if cmd is None:
            findings.append(Finding(filename, f"unknown verb: bifrost {sub} {verb}"))
            return findings
        valid = set()
        for p in cmd.params:
            valid.update(getattr(p, "opts", []))
            valid.update(getattr(p, "secondary_opts", []))
        for tok in tokens[3:]:
            if tok.startswith("--") and tok.split("=")[0] not in valid:
                findings.append(Finding(filename, f"unknown flag {tok} on bifrost {sub} {verb}"))
    return findings


def lint_text(text: str, filename: str) -> list[Finding]:
    groups = _load_groups()
    findings: list[Finding] = []
    invocations: list[tuple[str, str]] = []  # (command-line, mode)
    for m in FENCE.finditer(text):
        mode = _block_mode(filename, m.group("info"))
        for line in m.group("body").splitlines():
            line = line.strip()
            if line.startswith("bifrost "):
                invocations.append((line, mode))
    for m in INLINE.finditer(text):
        invocations.append((m.group(1), _block_mode(filename, "")))
    for line, mode in invocations:
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        findings.extend(_validate_invocation(tokens, mode, filename, groups))
    return findings


def lint_paths(paths: list[Path]) -> list[Finding]:
    out: list[Finding] = []
    for p in paths:
        rel = str(p.relative_to(REPO)) if p.is_absolute() else str(p)
        out.extend(lint_text(p.read_text(), rel))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_skill_cli_claims.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Confirm it goes red against the CURRENT skill**

Run: `cd api && PYTHONPATH=. python -c "from pathlib import Path; import sys; sys.path.insert(0,'../scripts/skill-truth'); import lint_claims as l; fs=l.lint_paths([Path('../.claude/skills/bifrost-build/SKILL.md')]); print(len(fs)); [print(f.message) for f in fs[:10]]"`
Expected: non-zero findings, including `bifrost watch` / `export` style bans (proves the linter catches the real gap before we fix the skill).

- [ ] **Step 6: Commit**

```bash
git add api/scripts/skill-truth/lint_claims.py api/tests/unit/test_skill_cli_claims.py
git commit -m "feat(skill-truth): CLI-claims linter with mode-conditional bans"
```

---

## Task 3: Codex sync script (both roots) + symlink normalization

**Files:**
- Create: `scripts/sync-codex-skills.sh`
- Modify: `skills/migrate` (→ symlink) — verify against `api/bifrost/skill.py` allowlist logic first
- Test: manual `diff -r` + a pytest guard

- [ ] **Step 1: Determine the intended split**

Read `.agents/plugins/marketplace.json`, `plugins/bifrost/.codex-plugin/plugin.json` (`"skills": "./skills/"`), and the contents of `plugins/bifrost/skills/` (4 public) vs `.codex/skills/` (8 maintainer). Confirm: `plugins/bifrost/skills/` mirrors the **public** set (those symlinked from top-level `skills/`); `.codex/skills/` mirrors the **maintainer** set (the non-symlinked `.claude/skills/bifrost-*`). Write this split as a comment block at the top of the sync script.

- [ ] **Step 2: Write the sync script**

```bash
#!/usr/bin/env bash
# Regenerate the Codex plain-file skill mirrors from .claude/skills/ (Codex
# can't follow symlinks). Two roots:
#   plugins/bifrost/skills/  <- PUBLIC set (skills symlinked from top-level skills/)
#   .codex/skills/           <- MAINTAINER set (the rest of .claude/skills/bifrost-*)
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

public=()
for link in skills/*; do
  [ -L "$link" ] || continue
  target="$(readlink "$link")"
  public+=("$(basename "$target")")
done

mkdir -p plugins/bifrost/skills .codex/skills
for d in .claude/skills/bifrost-*; do
  name="$(basename "$d")"
  if printf '%s\n' "${public[@]}" | grep -qx "$name"; then
    rsync -a --delete "$d/" "plugins/bifrost/skills/$name/"
  else
    rsync -a --delete "$d/" ".codex/skills/$name/"
  fi
done
echo "Codex mirrors synced."
```

Make executable: `chmod +x scripts/sync-codex-skills.sh`.

- [ ] **Step 3: Verify `bifrost skill update` allowlist still works if migrate becomes a symlink**

Run: `cd api && PYTHONPATH=. python -c "import bifrost.skill"` (import sanity), then read `api/bifrost/skill.py:233-260` to confirm symlink targets are read by basename. If `skills/migrate` as a real dir is load-bearing for any reason, leave it and add a comment in the sync script documenting the exception. Otherwise:
`rm -rf skills/migrate && ln -s ../.claude/skills/bifrost-migrate skills/migrate` (only if `bifrost-migrate` exists under `.claude/skills/`; today it's under `skills/migrate` and `plugins/bifrost/skills/bifrost-migrate` — decide canonical home and document it).

- [ ] **Step 4: Run the sync + diff**

Run: `./scripts/sync-codex-skills.sh && diff -r .claude/skills/bifrost-build plugins/bifrost/skills/bifrost-build && echo CLEAN`
Expected: `CLEAN` (mirror matches source).

- [ ] **Step 5: Commit**

```bash
git add scripts/sync-codex-skills.sh plugins/bifrost/skills/ .codex/skills/ skills/migrate
git commit -m "feat(skill-truth): Codex mirror sync (both roots) + symlink normalization"
```

---

## Task 4: CI `skill-accuracy` job (all three gates)

**Files:**
- Modify: `.github/workflows/ci.yml`
- Test: deliberate-drift manual verification

- [ ] **Step 1: Add the job (path-filtered)**

Add a `skill-accuracy` job to `.github/workflows/ci.yml`, path-filtered to `skills/**, .claude/skills/**, plugins/bifrost/**, .codex/skills/**, api/bifrost/**, client/src/lib/app-sdk/**, api/src/routers/**`, always-on for release tags. Steps:
1. Boot the test stack (or use the DB-free path if `app.openapi()` imports without a DB — verify; fallback: piggyback the stack-booting job).
2. `python api/scripts/skill-truth/generate.py --check` (Gate 1).
3. `./test.sh tests/unit/test_skill_cli_claims.py tests/unit/test_skill_appendix_fresh.py tests/unit/test_skill_reference_freshness.py` (Gate 2 + freshness).
4. `./scripts/sync-codex-skills.sh && git diff --exit-code plugins/bifrost/skills .codex/skills` (Gate 3).

(Follow the existing job structure in `ci.yml`; reuse the test-stack boot pattern already present. Copy the exact `runs-on`, cache, and stack-boot steps from the nearest existing unit-test job.)

- [ ] **Step 2: Verify red-on-drift locally (proxy for CI)**

Run: append `\`\`\`bash\nbifrost watch\n\`\`\`` to a scratch copy of a reference file and run the claims linter — confirm it reports. Run `generate.py --check` after hand-editing a `generated/*.md` — confirm STALE. Run the sync + `git diff` after hand-editing a mirror — confirm diff.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: skill-accuracy job — appendix freshness, claims linter, Codex mirror"
```

---

## Task 5: Hub `SKILL.md` rewrite (the dispatcher)

**Files:**
- Modify: `.claude/skills/bifrost-build/SKILL.md`

- [ ] **Step 1: Rewrite SKILL.md as the thin hub (≤ ~250 lines)**

Structure (prose, not code — but every `bifrost …` example must pass the claims linter):
1. Front-matter (unchanged name/description, or refine description to mention solution/repo modes).
2. **Prerequisites** — the existing `BIFROST_*` env block (carried over), minus the "Download Platform Docs" step (killed in Task 8).
3. **Mode detection (the dispatcher):**
   > Walk up from cwd for `bifrost.solution.yaml`. **Found → you are in a Solution workspace → read `references/solutions.md` and follow it.** Not found → global `_repo` workspace → read `references/repo.md`.
4. **Global hard rules:** never `bifrost watch/push/pull/sync` or `bifrost git *`; in a Solution, entities are deploy-owned (live `bifrost <entity> create|update` 409s — see solutions.md); confirm the org + access tuple before scaffolding (keep the existing access-tuple section verbatim — it's still correct).
5. **Routing index:** a short table mapping need → reference file (app → web-sdk-v2 + apps; workflow → workflows-python; table → tables; exact flag → generated/cli-reference; endpoint → generated/openapi-digest).

- [ ] **Step 2: Lint the rewritten hub**

Run: `cd api && PYTHONPATH=. python -c "from pathlib import Path; import sys; sys.path.insert(0,'../scripts/skill-truth'); import lint_claims as l; fs=l.lint_paths([Path('../.claude/skills/bifrost-build/SKILL.md')]); print('FINDINGS', len(fs)); [print(f.message) for f in fs]"`
Expected: `FINDINGS 0`.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/bifrost-build/SKILL.md
git commit -m "feat(build-skill): rewrite SKILL.md as solution/repo dispatcher hub"
```

---

## Task 6: `references/tables.md` (the pain-point deliverable)

**Files:**
- Create: `.claude/skills/bifrost-build/references/tables.md`

- [ ] **Step 1: Author the Python↔Web side-by-side**

Use the table in spec §/06-09-plan §3 (the verified signatures). Every signature MUST match `generated/python-sdk-signatures.md` and `generated/web-sdk-surface.md` verbatim. Cover: create (Python/CLI-only), delete (★ Python deletes TABLE, Web deletes ROW(S)), insert/insert_batch vs array-batch, upsert arg shape, update merge-patch, get (error models differ), query (kwargs vs options object; nested `.documents[].data` vs flattened `useTable` rows), count (filtered count Python-only), filter ops (`in_` vs `in`/`neq`), live updates (Web-only `subscribe`/`useTable`/`useInfiniteTable`). Add the scope/solution cascade note (`tables.py` `_scope_query`; web `setDefaultAppScope`).

- [ ] **Step 2: Lint**

Run the claims linter against `references/tables.md`. Expected: 0 findings.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/bifrost-build/references/tables.md
git commit -m "feat(build-skill): tables.md Python↔Web side-by-side reference"
```

---

## Task 7: `references/solutions.md` (LIGHT) + `repo.md` (entry)

**Files:**
- Create: `.claude/skills/bifrost-build/references/solutions.md`
- Create: `.claude/skills/bifrost-build/references/repo.md`

- [ ] **Step 1: Author `solutions.md` (LIGHT)**

Cover ONLY: the v2 lifecycle (`bifrost solution init` → `scaffold-app` → `start` → `deploy`); the read-only / deploy-is-full-replace invariant stated LOUD; the 7-export v2 SDK surface (link to `web-sdk-v2.md` for depth); the **getting-entities-into-a-solution mechanism** (TBD-by-validation — Task 11 pins it down; until then mark with the spec's open-question note); and a prominent pointer: "for the worked v1→v2 path, use the `bifrost:migrate` skill." Link into shared topic files; restate no shared facts. All `bifrost …` examples must be solution-legal (no live entity mutation; the linter will catch violations because this file is solution-context).

- [ ] **Step 2: Author `repo.md` (entry)**

Carry today's v1/global-workspace flow (the salvageable parts of the current SKILL.md): live entity mutation via `bifrost <entity> create|update` (correct here), workflow `.py` authoring, the watch/sync caveats (as legacy `_repo` tooling), and a link to `mcp-mode.md` for MCP-only. Link into shared topic files.

- [ ] **Step 3: Lint both**

Run the claims linter against both files. Expected: 0 findings (entity mutation allowed in repo.md, banned in solutions.md).

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/bifrost-build/references/solutions.md .claude/skills/bifrost-build/references/repo.md
git commit -m "feat(build-skill): solutions.md (light) + repo.md entry docs"
```

---

## Task 8: Remaining shared references + kill `docs/llm.txt`

**Files:**
- Create: `.claude/skills/bifrost-build/references/{workflows-python,web-sdk-v2,python-sdk,entities,apps,rest-api,mcp-mode}.md`
- Move: `import-patterns.md`, `platform-api.md` → `references/`; merge `app-patterns.md` → `apps.md`
- Delete: `docs/llm.txt`
- Modify: `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Author the shared topic files**

`workflows-python.md` (decorators, offline `bifrost run`, register/replace/remap, requirements); `web-sdk-v2.md` (BifrostProvider, `useWorkflow(path::fn)`, useWorkflowQuery/Mutation, useTable/useInfiniteTable, BifrostHeader, scaffold anatomy, tokenless dev); `python-sdk.md` (module prose; signatures live in `generated/`); `entities.md` (per-entity CLI verbs — **salvage the good prose from `docs/llm.txt` here**); `apps.md` (merge `app-patterns.md`, v2-first); `rest-api.md` (`bifrost api` boundaries, executions); `mcp-mode.md` (MCP-only flow + verified tool names from the tools modules). Move `import-patterns.md` + `platform-api.md` into `references/` unchanged (v1 refs).

- [ ] **Step 2: Delete llm.txt + repoint docs**

```bash
git rm docs/llm.txt
```
Edit `CLAUDE.md` and `AGENTS.md`: replace the `docs/llm.txt` references with "change a command → regenerate via `api/scripts/skill-truth/generate.py`; CI enforces freshness."

- [ ] **Step 3: Lint all new references + run appendix freshness**

Run the claims linter over `skills/**/*.md`; run `./test.sh tests/unit/test_skill_appendix_fresh.py`. Expected: 0 findings, PASS.

- [ ] **Step 4: Commit**

```bash
git add -A .claude/skills/bifrost-build/references/ CLAUDE.md AGENTS.md
git rm docs/llm.txt
git commit -m "feat(build-skill): shared reference files; salvage + kill docs/llm.txt"
```

---

## Task 9: Reference-freshness manifest

**Files:**
- Create: `.claude/skills/bifrost-build/references/sources.yaml`
- Create: `api/tests/unit/test_skill_reference_freshness.py`

- [ ] **Step 1: Write the failing test (soft staleness warn)**

```python
# api/tests/unit/test_skill_reference_freshness.py
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / ".claude/skills/bifrost-build/references/sources.yaml"


def _commits_since(sha: str, globs: list[str]) -> int:
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "--oneline", f"{sha}..HEAD", "--", *globs],
        capture_output=True, text=True,
    )
    return len([l for l in out.stdout.splitlines() if l.strip()])


def test_manifest_covers_all_reference_files():
    entries = yaml.safe_load(MANIFEST.read_text())
    documented = {e["file"] for e in entries}
    ref_dir = REPO / ".claude/skills/bifrost-build/references"
    curated = {
        f"references/{p.name}" for p in ref_dir.glob("*.md")
    }
    # every curated reference file must have a manifest entry
    assert curated <= documented, f"unmanifested: {curated - documented}"


def test_staleness_is_reported_not_fatal(capsys):
    entries = yaml.safe_load(MANIFEST.read_text())
    stale = []
    for e in entries:
        n = _commits_since(e["verified_at_sha"], e["source_globs"])
        if n:
            stale.append(f"{e['file']}: {n} commits since verify")
    # Soft gate: print, never assert. Visible in CI logs.
    if stale:
        print("REFERENCE STALENESS (informational):\n" + "\n".join(stale))
    assert True
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_skill_reference_freshness.py -v`
Expected: FAIL — `sources.yaml` missing.

- [ ] **Step 3: Write the manifest**

One entry per curated reference file. Example:

```yaml
# .claude/skills/bifrost-build/references/sources.yaml
- file: references/tables.md
  source_globs: ["api/bifrost/tables.py", "client/src/lib/app-sdk/tables.ts", "api/src/routers/tables.py"]
  verified_at_sha: HEAD   # replace with the actual sha after Task 11 drives tables
- file: references/web-sdk-v2.md
  source_globs: ["client/src/lib/app-sdk/index.v2.ts", "client/src/lib/app-sdk/*.ts"]
  verified_at_sha: HEAD
- file: references/workflows-python.md
  source_globs: ["api/bifrost/decorators.py", "api/bifrost/workflows.py", "api/bifrost/commands/workflows.py"]
  verified_at_sha: HEAD
- file: references/python-sdk.md
  source_globs: ["api/bifrost/*.py"]
  verified_at_sha: HEAD
- file: references/solutions.md
  source_globs: ["api/bifrost/commands/solution.py"]
  verified_at_sha: HEAD
- file: references/entities.md
  source_globs: ["api/bifrost/commands/*.py"]
  verified_at_sha: HEAD
- file: references/apps.md
  source_globs: ["api/bifrost/commands/apps.py", "client/src/lib/app-sdk/*.ts"]
  verified_at_sha: HEAD
- file: references/repo.md
  source_globs: ["api/bifrost/cli.py", "api/bifrost/commands/*.py"]
  verified_at_sha: HEAD
- file: references/rest-api.md
  source_globs: ["api/src/routers/*.py"]
  verified_at_sha: HEAD
- file: references/mcp-mode.md
  source_globs: ["api/src/services/mcp_server/tools/*.py"]
  verified_at_sha: HEAD
- file: references/import-patterns.md
  source_globs: ["api/bifrost/platform_names.py"]
  verified_at_sha: HEAD
- file: references/platform-api.md
  source_globs: ["client/src/lib/app-sdk/*.ts"]
  verified_at_sha: HEAD
```

Replace each `HEAD` with `git rev-parse HEAD` at authoring time.

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_skill_reference_freshness.py -v`
Expected: PASS (both tests; staleness test prints nothing since shas are current).

- [ ] **Step 5: Document the `diff`-mode re-verify path**

Add a short "Maintaining these references" section to `SKILL.md` (or a `references/MAINTENANCE.md`): when the staleness check warns, re-drive only the changed surface against the debug stack (reuse the validation harness scoped down), fix the prose, bump `verified_at_sha`. Mirror the `bifrost-documentation` skill's `diff` mode explicitly.

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/bifrost-build/references/sources.yaml api/tests/unit/test_skill_reference_freshness.py .claude/skills/bifrost-build/SKILL.md
git commit -m "feat(build-skill): reference-freshness manifest + soft staleness check"
```

---

## Task 10: Distribution check + plugin version bump

**Files:**
- Modify: plugin manifests (via script)

- [ ] **Step 1: `bifrost skill update` round-trip (nested dirs)**

From a scratch dir, install the branch tarball via `bifrost skill update` against the branch (or simulate `_fetch_skill_files` + `_write_skill` against a local tarball). Confirm `references/*.md` and `generated/*.md` materialize on disk under the target. (Verified statically in the spec; this confirms it end-to-end.)

- [ ] **Step 2: Plugin load sanity**

Confirm the Claude plugin loads the skill (symlink resolves) and the Codex mirrors are present in both roots.

- [ ] **Step 3: Bump the version**

Run: `scripts/update-plugin-version.sh "$(scripts/compute-dev-version.sh)"`
Verify all manifests bumped (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `plugins/bifrost/.codex-plugin/plugin.json`).

- [ ] **Step 4: Commit**

```bash
git add .claude-plugin/plugin.json .codex-plugin/plugin.json plugins/bifrost/.codex-plugin/plugin.json
git commit -m "chore(plugin): bump version for build-skill rebuild"
```

---

## Task 11: Sonnet validation loop — Track A (solution) + full SDK coverage

> This task is **orchestration**, not file-by-file TDD. It is the spec's centerpiece (§7) and *defines done* for the skill content. Run it AFTER Tasks 0–10 are green.

**Files:**
- Create/append: `docs/plans/2026-06-15-build-skill-validation-log.md`
- Modify (as findings demand): any `references/*.md`, `SKILL.md`

- [ ] **Step 1: Boot the debug stack in port mode**

Run: `BIFROST_FORCE_PORT=1 ./debug.sh up && ./debug.sh status`. Capture the URL. (Port mode is mandatory — Chrome can't drive netbird Vite.)

- [ ] **Step 2: Build the SDK-surface coverage checklist**

From `generated/python-sdk-signatures.md` + `generated/web-sdk-surface.md`, enumerate every public Python SDK method and every v2 web export into a checklist in the validation log. This is the coverage target — the union of Track A + Track B must tick every box.

- [ ] **Step 3: Run one Sonnet build (fresh subagent, skill-only guidance)**

Dispatch a `general-purpose` subagent on `model: sonnet`, isolation `worktree` (or a clean `/tmp/bifrost-build-validation-A-<n>` scratch dir), instructed: install the API-matched CLI in a scratch venv (per CLAUDE.md), follow ONLY the `bifrost:build` skill, build a solution from scratch — `solution init` → scaffold a Tailwind-styled app → get an agent + table + form/config into the solution → `solution start` + drive every page → update an entity → `solution deploy`. Exercise as much of the SDK surface as the build naturally touches; report every misleading skill moment and which SDK ops it drove.

- [ ] **Step 4: Score + log**

Record the scorecard (styled / entities correct / update / deploy clean / invariant respected) and tick the coverage checklist. Every misleading moment → a skill-doc fix in this session → **reset the consecutive-clean counter to 0**.

- [ ] **Step 5: Loop to the done bar**

Repeat Steps 3–4 until **3 consecutive clean runs with no doc edits between them**. The open question (how to get an agent/form/table into a solution — capture vs deploy-manifest) MUST be pinned down here and written into `solutions.md`; then bump that file's `verified_at_sha` in `sources.yaml`.

- [ ] **Step 6: Commit**

```bash
git add docs/plans/2026-06-15-build-skill-validation-log.md .claude/skills/bifrost-build/
git commit -m "validate(build-skill): Track A (solution) — 3-clean streak + SDK coverage"
```

---

## Task 12: Sonnet validation loop — Track B (repo/global) + coverage closeout

> Orchestration. Covers any SDK surface Track A didn't reach, so the union drives the whole SDK.

- [ ] **Step 1: Run one Sonnet repo build (fresh subagent)**

Dispatch a fresh `sonnet` subagent in a clean scratch dir, skill-only guidance, in a **non-solution** (global `_repo`) workspace: author a workflow `.py`, create entities via live `bifrost <entity> create|update` (correct here), execute the workflow, iterate. Target the coverage-checklist boxes Track A left unticked. If cheap, run an MCP-only variant.

- [ ] **Step 2: Score + log + loop to the done bar**

Same scorecard + coverage ticks; every misleading moment → doc fix → reset. Loop to **3 consecutive clean runs, no doc edits between**.

- [ ] **Step 3: Coverage closeout**

Confirm every box on the SDK-surface checklist is ticked by A ∪ B. Any still-unreached op is a logged gap with a reason (e.g. "no runtime path in either track" → note it; do not silently drop). Bump `verified_at_sha` for every reference file whose claims were driven.

- [ ] **Step 4: Full pre-completion verification**

Run the CLAUDE.md pre-completion sequence: `cd api && pyright && ruff check .`; regen client types if touched; `./test.sh all`; `./test.sh client unit`. Run the `skill-accuracy` gates locally (`generate.py --check`, the three skill tests, sync + `diff`). All green.

- [ ] **Step 5: Commit**

```bash
git add docs/plans/2026-06-15-build-skill-validation-log.md .claude/skills/bifrost-build/
git commit -m "validate(build-skill): Track B (repo) — 3-clean streak + full SDK coverage closeout"
```

---

## Self-review notes (for the executor)

- **Generator interpreter:** Tasks 0–1 import `bifrost.*` and `src.*`. These resolve in the test-stack API container / `api/` with `PYTHONPATH=.`. If `app.openapi()` needs a DB, run the OpenAPI digest step inside the booted stack (see Task 4 Step 1 fallback).
- **`ts-morph` dependency:** Task 1 may add a client dev dep. If the team prefers no new dep, swap `dump-app-sdk-surface.mjs` for a `tsc --emitDeclarationOnly` + `.d.ts` parse — but `ts-morph` is the lower-risk path.
- **The open mechanism question** (entities-into-a-solution) is intentionally unresolved until Task 11 drives it; `solutions.md` carries the spec's open-question note until then. Do not guess it earlier.
- **Tasks 11–12 are not TDD.** They are the empirical proof; their "tests" are the live builds + the scorecard + the coverage checklist.
