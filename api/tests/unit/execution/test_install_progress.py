"""Unit tests for the install-progress aggregator."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch

from src.services.execution.install_progress import (
    WorkerPhase,
    aggregate_phases,
    summary_line,
)


def test_aggregate_counts_phases_out_of_total():
    phases = {
        "w1": WorkerPhase(phase="installed"),
        "w2": WorkerPhase(phase="installing"),
        "w3": WorkerPhase(phase="failed", package="xhtml2pdf", error="no cc"),
    }
    agg = aggregate_phases(phases, total=4)
    assert agg["total"] == 4
    assert agg["installed"] == 1
    assert agg["installing"] == 1
    assert agg["failed"] == 1
    assert agg["failures"] == [{"worker": "w3", "package": "xhtml2pdf", "error": "no cc"}]


def test_summary_line_installing():
    agg = {"total": 6, "installing": 3, "installed": 0, "recycling": 0,
           "recycled": 0, "failed": 0, "failures": []}
    assert summary_line(agg, action="install") == "Installing on 3/6 workers…"


def test_summary_line_complete_with_failures():
    agg = {"total": 6, "installing": 0, "installed": 5, "recycling": 0,
           "recycled": 5, "failed": 1,
           "failures": [{"worker": "w4", "package": "xhtml2pdf", "error": "no cc"}]}
    line = summary_line(agg, action="install")
    assert "5/6" in line
    assert "xhtml2pdf" in line


def test_summary_line_done_uses_folded_installed_count():
    # 3 done total: but raw recycled=1. Folded installed must win → 3/3.
    agg = {"total": 3, "installing": 0, "installed": 3, "recycling": 0,
           "recycled": 1, "failed": 0, "failures": []}
    assert summary_line(agg, action="install") == "Installed on 3/3 workers"


@pytest.mark.asyncio
async def test_report_phase_writes_hash_and_publishes_once():
    fake_redis = AsyncMock()
    fake_redis.hgetall.return_value = {
        "w1": json.dumps({"phase": "installed"}),
        "w2": json.dumps({"phase": "installing"}),
    }
    fake_redis.scan.side_effect = [(0, ["bifrost:pool:w1", "bifrost:pool:w2"])]

    published: list[dict] = []

    async def fake_broadcast(channel, message):
        published.append(message)

    from src.services.execution import install_progress as ip
    with patch.object(ip, "_raw_redis", AsyncMock(return_value=fake_redis)), \
         patch.object(ip.pubsub_manager, "broadcast", side_effect=fake_broadcast):
        await ip.report_phase(
            run_id="run1", worker_id="w1", phase="installed", action="install"
        )

    fake_redis.hset.assert_awaited_once()
    call_args = fake_redis.hset.await_args
    assert call_args.args[0] == "bifrost:pkg-install:run1"
    assert call_args.args[1] == "w1"
    assert "installed" in call_args.args[2]
    assert len(published) == 1
    assert published[0]["type"] == "progress"
    assert published[0]["total"] == 2
    assert published[0]["installed"] == 1
    assert published[0]["line"] == "Installing on 1/2 workers…"
