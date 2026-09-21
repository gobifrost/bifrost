"""Durability contracts for the workspace-bundle platform job."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _context(user_id: str = "owner"):
    return SimpleNamespace(requested_by_user_id=user_id)


def _payload():
    from src.jobs.platform.workspace_bundle_import import WorkspaceBundleImportPayload

    return WorkspaceBundleImportPayload(preview_id=uuid4(), package_sha256="a" * 64, decisions=[])


def test_workspace_bundle_job_allows_cancellation_before_commit() -> None:
    from src.jobs.platform.workspace_bundle_import import WORKSPACE_BUNDLE_IMPORT_DEFINITION

    assert WORKSPACE_BUNDLE_IMPORT_DEFINITION.policy.allow_running_cancellation is True
    assert WORKSPACE_BUNDLE_IMPORT_DEFINITION.policy.retry_on_failure is True


def test_workspace_bundle_preview_guard_rejects_other_requester() -> None:
    from src.jobs.platform.workspace_bundle_import import _require_requester
    from src.jobs.platform.base import PlatformJobFailure

    payload = _payload()
    with pytest.raises(PlatformJobFailure, match="another user"):
        _require_requester(
            {"requested_by": "other", "package_sha256": payload.package_sha256,
             "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()},
            _context(), payload,
        )


def test_workspace_bundle_preview_guard_rejects_expired_preview() -> None:
    from src.jobs.platform.workspace_bundle_import import _require_requester
    from src.jobs.platform.base import PlatformJobFailure

    payload = _payload()
    with pytest.raises(PlatformJobFailure, match="expired"):
        _require_requester(
            {"requested_by": "owner", "package_sha256": payload.package_sha256,
             "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
            _context(), payload,
        )
