"""Regression coverage for bounded workspace tree traversal."""

from __future__ import annotations

import multiprocessing
import resource
import sys
from pathlib import Path

import pytest

from src.services.git_repo_manager import iter_tree_metadata


MIB = 1024 * 1024


def _scan_peak_rss(root: str, queue: multiprocessing.Queue) -> None:
    """Scan in a fresh process so the peak excludes the test runner baseline."""
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    entries = list(iter_tree_metadata(Path(root)))
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    multiplier = 1 if sys.platform == "darwin" else 1024
    queue.put((len(entries), max(0, after - before) * multiplier))


@pytest.mark.slow
def test_tree_scan_memory_is_bounded(tmp_path: Path) -> None:
    """Metadata traversal never retains the content of a 512 MiB workspace tree."""
    with (tmp_path / "large.bin").open("wb") as large_file:
        large_file.truncate(512 * MIB)
    (tmp_path / "small.txt").write_text("small workspace file")

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_scan_peak_rss, args=(str(tmp_path), queue))
    process.start()
    process.join(timeout=90)

    assert process.exitcode == 0
    entry_count, peak_delta = queue.get(timeout=5)
    assert entry_count == 2
    assert peak_delta < 96 * MIB
