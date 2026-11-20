"""Pure input-boundary tests for the API-container bridge."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cloudflare_live_api_bridge", HERE / "api_bridge.py")
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)

WORKER = "11111111-2222-4333-8444-555555555555"
RESERVATION = {
    "worker_id": WORKER,
    "worker_version": "build-1",
    "memory_limit_bytes": 4 * 1024**3,
    "max_lifetime_seconds": 3600,
    "drain_grace_seconds": 300,
}


def test_identity_requires_the_api_scope_sentinel_and_exact_bound_values() -> None:
    value = {"scope": "suite", "worker_id": WORKER, "resource_uid": "resource-1", "reservation": RESERVATION}
    with patch.dict(os.environ, {"BIFROST_CONFORMANCE_SCOPE": "suite"}, clear=True):
        scope, worker_id, resource_uid, reservation = bridge._identity(value)

    assert scope == "suite"
    assert str(worker_id) == WORKER
    assert resource_uid == "resource-1"
    assert reservation == RESERVATION


def test_identity_refuses_wrong_scope_or_malformed_resource_uid() -> None:
    value = {"scope": "suite", "worker_id": WORKER, "resource_uid": "resource-1", "reservation": RESERVATION}
    with patch.dict(os.environ, {"BIFROST_CONFORMANCE_SCOPE": "other"}, clear=True):
        with pytest.raises(bridge.BridgeError, match="scope"):
            bridge._identity(value)
    with patch.dict(os.environ, {"BIFROST_CONFORMANCE_SCOPE": "suite"}, clear=True):
        with pytest.raises(bridge.BridgeError, match="resource"):
            bridge._identity({**value, "resource_uid": "bad uid"})
