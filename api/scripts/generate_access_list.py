#!/usr/bin/env python3
"""Generate the access-list JSON projection from the hand-curated data module.

Mirrors ``scripts/operation_catalog/generate.py``: the data lives in Python
(``src.services.access_list``), and this script projects it to a stable,
sorted JSON file for tooling/CI to diff against.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent
sys.path.insert(0, str(API_ROOT))
os.environ.setdefault(
    "BIFROST_SECRET_KEY",
    "access-list-generation-only-secret-key",
)

from src.services.access_list import ACCESS_LIST  # noqa: E402


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


OUTPUT_PATH = _resolve_repo_path(Path("docs/generated/access-list.json"))


def _entry_dict(entry: object) -> dict:
    return entry.model_dump(mode="json")  # type: ignore[attr-defined]


def render() -> str:
    rows = [_entry_dict(entry) for entry in ACCESS_LIST]
    rows.sort(
        key=lambda row: (
            0 if row.get("mcp_tool") is None else 1,
            row.get("path") or "",
            row.get("method") or "",
            row.get("mcp_tool") or "",
        )
    )
    return json.dumps(rows, indent=2, sort_keys=True) + "\n"


def _check(path: Path, expected: str) -> bool:
    return path.is_file() and path.read_text(encoding="utf-8") == expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = render()
    if args.check:
        if not _check(OUTPUT_PATH, output):
            print(f"Stale generated access list: {OUTPUT_PATH}", file=sys.stderr)
            return 1
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
