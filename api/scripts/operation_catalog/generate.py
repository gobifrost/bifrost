#!/usr/bin/env python3
"""Generate operation inventory and compact bifrost-build operation reference."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parent
sys.path.insert(0, str(API_ROOT))
os.environ.setdefault(
    "BIFROST_SECRET_KEY",
    "operation-catalog-generation-only-secret-key",
)

from src.main import app  # noqa: E402
from src.services.operation_catalog import OPERATION_CATALOG  # noqa: E402
from src.services.operation_inventory import build_operation_inventory  # noqa: E402


def _resolve_repo_path(relative: Path) -> Path:
    """Resolve host-repo and Docker test-runner mount layouts."""

    host_layout = REPO_ROOT / relative
    if (
        host_layout.parent.exists()
        or (REPO_ROOT / "docs").is_dir()
        or (REPO_ROOT / ".git").exists()
    ):
        return host_layout
    return API_ROOT / relative


INVENTORY_PATH = _resolve_repo_path(
    Path("docs/generated/operation-surface-inventory.json")
)
OPERATIONS_PATH = _resolve_repo_path(
    Path(".claude/skills/bifrost-build/generated/operations.md")
)


def render_inventory() -> str:
    return json.dumps(
        build_operation_inventory(app, REPO_ROOT),
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_operations() -> str:
    lines = [
        "# Bifrost operation reference",
        "",
        "Generated from the canonical operation catalog. Use the stable intent",
        "ID when reasoning; select the CLI or MCP binding available in the current",
        "harness.",
        "",
        "| Intent | CLI | MCP | Scope |",
        "|---|---|---|---|",
    ]
    for operation in OPERATION_CATALOG:
        cli = (
            "`bifrost " + " ".join(operation.cli.path) + "`"
            if operation.cli
            else "—"
        )
        mcp = f"`{operation.mcp.name}`" if operation.mcp else "—"
        scopes = ", ".join(f"`{scope}`" for scope in operation.action_scopes) or "—"
        lines.append(f"| `{operation.operation_id}` | {cli} | {mcp} | {scopes} |")
    lines.append("")
    return "\n".join(lines)


def _check(path: Path, expected: str) -> bool:
    return path.is_file() and path.read_text(encoding="utf-8") == expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = {
        INVENTORY_PATH: render_inventory(),
        OPERATIONS_PATH: render_operations(),
    }
    if args.check:
        stale = [str(path) for path, value in outputs.items() if not _check(path, value)]
        if stale:
            print("Stale generated operation files: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    for path, value in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
