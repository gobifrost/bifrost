#!/usr/bin/env python3
"""Benchmark the queue-position publication no-op against the saved N2 control."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import resource_curve  # noqa: E402


CANDIDATE_IMAGE = "bifrost-api:kind-a74e45295998-queue-position-off-r1"
COUNTS = (2,)
RATES = (2.0, 4.0, 8.0)
BASE_PROFILE = "hosting-8gib-fixed-workers-1"


def run_candidate(image: str, description: str) -> None:
    """Run one fixed-image N2 queue-position diagnostic candidate."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--owner-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--table-fixture", required=True, type=Path)
    args = parser.parse_args()

    # Delegate locking, cleanup, provenance, and all workload gates to the
    # resource-curve runner. This arm varies only the candidate runtime image.
    resource_curve.FROZEN_IMAGE = image
    resource_curve.RATES = RATES
    sys.argv = [
        "resource_curve.py",
        "--owner-root", str(args.owner_root),
        "--api-image", image,
        "--out", str(args.out),
        "--table-fixture", str(args.table_fixture),
        "--base-profile", BASE_PROFILE,
        "--counts", ",".join(map(str, COUNTS)),
    ]
    resource_curve.main()


def main() -> None:
    run_candidate(CANDIDATE_IMAGE, __doc__)


if __name__ == "__main__":
    main()
