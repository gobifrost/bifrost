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


def gen_python_sdk_signatures() -> str:
    import importlib
    import inspect

    from src.services.mcp_server.tools import sdk as sdk_tools

    lines: list[str] = ["# Python SDK Signatures (generated — do not edit)\n"]
    lines.append("> Regenerate: `python api/scripts/skill-truth/generate.py`. CI enforces freshness.\n")

    sdk_modules = [
        "agents", "ai", "config", "events", "executions", "files", "forms",
        "integrations", "knowledge", "organizations", "roles", "tables",
        "users", "workflows",
    ]
    for mod_name in sorted(sdk_modules):
        try:
            mod = importlib.import_module(f"bifrost.{mod_name}")
        except ImportError:
            continue
        # Each SDK module exposes a class with the same name as the module.
        cls = getattr(mod, mod_name, None)
        if cls is None or not inspect.isclass(cls):
            continue
        doc = sdk_tools._generate_module_docs(mod_name, cls)
        if doc:
            lines.append(doc)

    return "\n".join(lines) + "\n"


def gen_openapi_digest() -> str:
    from src.main import app

    spec = app.openapi()
    lines: list[str] = [
        "# OpenAPI Digest (generated — do not edit)\n",
        "> Regenerate: `python api/scripts/skill-truth/generate.py`. CI enforces freshness.\n",
        "",
        "| Method | Path |",
        "|---|---|",
    ]
    rows: list[tuple[str, str]] = []
    for path, methods in spec.get("paths", {}).items():
        for method in methods:
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            rows.append((method.upper(), path))
    # Sort by (path, method) for deterministic output.
    # operationId is intentionally excluded: FastAPI assigns non-deterministic
    # suffixes to duplicate handler names across process restarts.
    for m, p in sorted(rows, key=lambda r: (r[1], r[0])):
        lines.append(f"| {m} | `{p}` |")
    return "\n".join(lines) + "\n"


def gen_web_sdk_surface() -> str:
    import subprocess

    script = Path(__file__).resolve().parent / "dump-app-sdk-surface.mjs"
    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


GENERATORS = {
    "cli-reference.md": gen_cli_reference,
    "openapi-digest.md": gen_openapi_digest,
    "python-sdk-signatures.md": gen_python_sdk_signatures,
    "web-sdk-surface.md": gen_web_sdk_surface,
}


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
