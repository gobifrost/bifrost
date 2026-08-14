#!/usr/bin/env python3
"""Build the CLI archive that the API image serves as a static artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

from shared.cli_artifact import build_cli_artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    artifact = build_cli_artifact(args.source, args.output, args.version)
    print(artifact)


if __name__ == "__main__":
    main()
