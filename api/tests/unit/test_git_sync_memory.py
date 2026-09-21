"""Regression coverage for bounded workspace tree traversal."""

from __future__ import annotations

import multiprocessing
import resource
import sys
import asyncio
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


def _index_text_files_peak_rss(root: str, queue: multiprocessing.Queue) -> None:
    """Exercise the file-index writer without retaining every changed file."""
    from src.services.github_sync import GitHubSyncService

    class EmptyResult:
        def all(self):
            return []

    class RecordingDb:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return EmptyResult()

    service = object.__new__(GitHubSyncService)
    service.db = RecordingDb()
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    asyncio.run(service._update_file_index(Path(root)))
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    multiplier = 1 if sys.platform == "darwin" else 1024
    queue.put((service.db.calls, max(0, after - before) * multiplier))


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


@pytest.mark.slow
def test_file_indexing_batches_large_changed_text_files(tmp_path: Path) -> None:
    """Changed text files are upserted in bounded batches, not one giant list."""
    for index in range(16):
        (tmp_path / f"document-{index}.txt").write_bytes(b"x" * (8 * MIB))

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_index_text_files_peak_rss, args=(str(tmp_path), queue))
    process.start()
    process.join(timeout=90)

    assert process.exitcode == 0
    execute_calls, peak_delta = queue.get(timeout=5)
    # One query prefetch plus several bounded insert batches; a single insert
    # batch would retain all 128 MiB of changed text before writing it.
    assert execute_calls > 2
    assert peak_delta < 96 * MIB


@pytest.mark.slow
def test_file_index_skips_sparse_huge_text_file_without_reading_it(tmp_path: Path) -> None:
    """An oversized text path is de-indexed without materializing its bytes."""
    with (tmp_path / "huge.txt").open("wb") as huge_file:
        huge_file.truncate(512 * MIB)

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_index_text_files_peak_rss, args=(str(tmp_path), queue))
    process.start()
    process.join(timeout=90)

    assert process.exitcode == 0
    execute_calls, peak_delta = queue.get(timeout=5)
    # One prefetch and one de-index query; loading the sparse file would add
    # hundreds of MiB to the child process RSS.
    assert execute_calls == 2
    assert peak_delta < 96 * MIB
