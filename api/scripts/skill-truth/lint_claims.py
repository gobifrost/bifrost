"""Validate every ``bifrost ...`` invocation in skill markdown against the real
Click tree. Globally-banned commands always fail; live entity mutation fails
only in solution context (file matches ``solutions.md`` or a ``bash solution``
fence block).

Run directly::

    python lint_claims.py path/to/SKILL.md [...]

Or import ``lint_text`` / ``lint_paths`` from a pytest wrapper.
"""
from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click

REPO = Path(__file__).resolve().parents[3]

# Commands that are never correct in any doc context.
GLOBAL_BANNED = {"watch", "push", "pull", "sync", "git", "export", "import"}

# Entity groups whose create/update/delete are deploy-owned in a solution
# workspace and would 409 if run live there.
SOLUTION_MANAGED_GROUPS = {
    "agents", "forms", "tables", "configs", "apps", "workflows", "events",
}
MUTATING_VERBS = {"create", "update", "delete"}

# Top-level subcommands that are NOT in ENTITY_GROUPS and NOT in solution_group.
# We skip flag-checking for these (they vary in implementation style).
# `help` is a real top-level alias (cli.py:631); `version` is only `--version`,
# not a bare subcommand, so it is intentionally NOT here.
_HANDROLLED_TOP = {
    "login", "logout", "auth", "run", "api", "migrate-imports", "skill", "deploy",
    "help",
}

FENCE = re.compile(r"```(?P<info>[^\n]*)\n(?P<body>.*?)```", re.DOTALL)
INLINE = re.compile(r"`(bifrost [^`]+)`")


@dataclass
class Finding:
    filename: str
    message: str


def _load_groups() -> dict[str, "click.Group"]:
    from bifrost.commands import ENTITY_GROUPS
    from bifrost.commands.solution import solution_group

    return {**ENTITY_GROUPS, "solution": solution_group}


def _block_mode(filename: str, info: str) -> str:
    """Return 'solution' or 'repo' for this fence block."""
    info = (info or "").strip().lower()
    if "solution" in info:
        return "solution"
    if "repo" in info:
        return "repo"
    if Path(filename).name == "solutions.md":
        return "solution"
    return "repo"


def _join_continuations(lines: list[str]) -> list[str]:
    """Merge shell line-continuations (trailing backslash) into single logical lines."""
    out: list[str] = []
    buf: list[str] = []
    for raw in lines:
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buf.append(stripped[:-1].rstrip())
        else:
            buf.append(stripped)
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def _validate_invocation(
    tokens: list[str],
    mode: str,
    filename: str,
    groups: dict,
) -> list[Finding]:
    findings: list[Finding] = []
    if not tokens or tokens[0] != "bifrost" or len(tokens) < 2:
        return findings

    sub = tokens[1]

    if sub in GLOBAL_BANNED:
        return [Finding(filename, f"globally-banned command: bifrost {sub}")]

    if (
        sub in SOLUTION_MANAGED_GROUPS
        and len(tokens) >= 3
        and tokens[2] in MUTATING_VERBS
        and mode == "solution"
    ):
        return [
            Finding(
                filename,
                f"live entity mutation forbidden in solution context: "
                f"bifrost {sub} {tokens[2]}",
            )
        ]

    grp = groups.get(sub)
    if grp is None:
        if sub not in _HANDROLLED_TOP:
            findings.append(Finding(filename, f"unknown command: bifrost {sub}"))
        return findings

    if len(tokens) >= 3:
        verb = tokens[2]
        cmd = grp.commands.get(verb)
        if cmd is None:
            findings.append(
                Finding(filename, f"unknown verb: bifrost {sub} {verb}")
            )
            return findings

        # Collect all valid --flags from the Click parameter list.
        valid: set[str] = set()
        for p in cmd.params:
            valid.update(getattr(p, "opts", []))
            # secondary_opts is always [] for Click Options but include defensively.
            valid.update(getattr(p, "secondary_opts", []))

        for tok in tokens[3:]:
            # Only check --flag tokens; skip positional args and --flag=value values.
            # (Positional-arg arity isn't validated: doc examples use quoted
            # multi-word values and trailing comments that make a reliable
            # positional count infeasible without a real Click parse.)
            flag = tok.split("=")[0]
            if flag.startswith("--") and flag not in valid:
                findings.append(
                    Finding(
                        filename,
                        f"unknown flag {tok} on bifrost {sub} {verb}",
                    )
                )

    return findings


def lint_text(text: str, filename: str) -> list[Finding]:
    """Lint all ``bifrost`` invocations in *text*, returning a list of findings."""
    groups = _load_groups()
    findings: list[Finding] = []
    invocations: list[tuple[str, str]] = []  # (line, mode)

    for m in FENCE.finditer(text):
        mode = _block_mode(filename, m.group("info"))
        raw_lines = m.group("body").splitlines()
        for line in _join_continuations(raw_lines):
            line = line.strip()
            # Skip comments and empty lines.
            if not line or line.startswith("#"):
                continue
            if line.startswith("bifrost ") or line == "bifrost":
                invocations.append((line, mode))

    for m in INLINE.finditer(text):
        invocations.append((m.group(1), _block_mode(filename, "")))

    for line, mode in invocations:
        try:
            tokens = shlex.split(line)
        except ValueError:
            # Unmatched quotes / shell constructs — skip unparseable lines.
            continue
        findings.extend(_validate_invocation(tokens, mode, filename, groups))

    return findings


def lint_paths(paths: list[Path]) -> list[Finding]:
    """Lint a list of markdown paths, returning all findings."""
    out: list[Finding] = []
    for p in paths:
        # Report repo-relative when the path is under REPO; otherwise fall back to
        # the raw path (avoids relative_to() raising on absolute paths outside REPO).
        rel = str(p.relative_to(REPO)) if p.is_absolute() and p.is_relative_to(REPO) else str(p)
        out.extend(lint_text(p.read_text(encoding="utf-8"), rel))
    return out


if __name__ == "__main__":
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        print("Usage: lint_claims.py <file.md> [...]", file=sys.stderr)
        sys.exit(1)
    all_findings = lint_paths(paths)
    for f in all_findings:
        print(f"{f.filename}: {f.message}")
    sys.exit(1 if all_findings else 0)
