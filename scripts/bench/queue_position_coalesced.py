#!/usr/bin/env python3
"""Benchmark coalesced queue-position refresh against the saved N2 control."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import queue_position_causal  # noqa: E402


CANDIDATE_IMAGE = "bifrost-api:kind-a74e45295998-queue-position-coalesced-r1"


def main() -> None:
    queue_position_causal.run_candidate(CANDIDATE_IMAGE, __doc__)


if __name__ == "__main__":
    main()
