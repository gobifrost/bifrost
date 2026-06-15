"""Regenerate .claude/skills/bifrost-build/generated/*.md from source.

Deterministic: sorted iteration, no timestamps. `--check` writes nothing and
diffs against the committed files, exiting non-zero on drift.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import click

# api/scripts/skill-truth/generate.py:
#   parents[0] = api/scripts/skill-truth  (or /app/scripts/skill-truth in container)
#   parents[1] = api/scripts              (or /app/scripts in container)
#   parents[2] = api/                     (or /app in container)
#   parents[3] = repo root                (or / in container, where /.claude/skills is mounted)
REPO = Path(__file__).resolve().parents[3]
GEN_DIR = REPO / ".claude/skills/bifrost-build/generated"


def _walk_group(name: str, group: click.Group, lines: list[str], depth: int = 0) -> None:
    ctx = click.Context(group, info_name=name)
    lines.append(f"{'#' * (depth + 2)} `{name}`\n")
    lines.append("```\n" + group.get_help(ctx).rstrip() + "\n```\n")
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
    if not args.check:
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
