"""Keep the installable Solution's GraphQL transport identical to the skill helper."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".claude/skills/bifrost-rewst-migration/scripts/rewst_graphql_export.py"
TARGET = ROOT / "examples/rewst-migration/modules/rewst_migration/graphql.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = SOURCE.read_bytes()
    if args.check:
        if not TARGET.is_file() or TARGET.read_bytes() != content:
            print(
                "Rewst Solution transport is stale; run python3 scripts/sync_rewst_migration_client.py"
            )
            return 1
        print("Rewst Solution transport matches the canonical helper")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(content)
    print("Updated Rewst Solution transport")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
