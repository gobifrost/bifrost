"""First-connect workspace Git contracts and pure reconciliation planning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models.contracts.github import GitConnectRequest


def test_connect_tree_classification_covers_detached_local_and_remote_content() -> None:
    from src.services.github_sync import classify_connect_trees

    items = classify_connect_trees(
        {
            "modules/local.py": "local",
            "modules/shared.py": "ours",
            "modules/same.py": "same",
        },
        {
            "modules/remote.py": "remote",
            "modules/shared.py": "theirs",
            "modules/same.py": "same",
        },
    )

    assert [(item.path, item.classification) for item in items] == [
        ("modules/local.py", "local_only"),
        ("modules/remote.py", "remote_only"),
        ("modules/same.py", "identical"),
        ("modules/shared.py", "conflict"),
    ]


def test_reconcile_requires_exactly_one_local_or_remote_choice_per_conflict() -> None:
    from src.models.contracts.github import GitConnectItem
    from src.services.github_sync import GitConnectDecisionError, resolve_connect_items

    items = [
        GitConnectItem(
            path="modules/shared.py",
            classification="conflict",
            local_sha256="ours",
            remote_sha256="theirs",
        )
    ]

    with pytest.raises(GitConnectDecisionError, match="modules/shared.py"):
        resolve_connect_items(items, strategy="reconcile", decisions={})
    assert resolve_connect_items(
        items,
        strategy="reconcile",
        decisions={"modules/shared.py": "local"},
    ) == {"modules/shared.py": "local"}


def test_connect_preview_rejects_file_directory_shape_collisions() -> None:
    """A file cannot be reconciled with a directory rooted at the same path."""
    from src.services.github_sync import GitConnectPreviewError, classify_connect_trees

    with pytest.raises(GitConnectPreviewError, match="file/directory shape conflict.*modules/shared"):
        classify_connect_trees(
            {"modules/shared": "local-file"},
            {"modules/shared/task.py": "remote-file"},
        )


def test_start_from_remote_requires_explicit_confirmation_when_local_content_would_be_discarded() -> None:
    request = GitConnectRequest(
        preview_token="preview",
        strategy="start_from_remote",
        confirm_destructive=False,
    )

    assert request.confirm_destructive is False


@pytest.mark.asyncio
async def test_connect_preview_token_is_bound_to_requester_organization_and_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.github_sync import (
        GitConnectPreviewError,
        GitHubSyncService,
        _GitConnectPreviewRecord,
    )

    class Redis:
        def __init__(self, raw: str) -> None:
            self.raw = raw
            self.deleted = False

        async def get(self, _key: str) -> str:
            return self.raw

        async def delete(self, _key: str) -> None:
            self.deleted = True

    record = _GitConnectPreviewRecord(
        token="token",
        repository_url="https://github.com/acme/workspace",
        branch="main",
        requested_by_user_id="owner",
        organization_id="org",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        local_fingerprint="local",
        remote_fingerprint="remote",
        remote_head_sha=None,
        items=[],
    )
    redis = Redis(record.model_dump_json())

    async def get_redis() -> Redis:
        return redis

    monkeypatch.setattr("src.core.cache.redis_client.get_shared_redis", get_redis)

    with pytest.raises(GitConnectPreviewError, match="not found"):
        await GitHubSyncService.load_connect_preview(
            "token", requested_by_user_id="other", organization_id="org"
        )

    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    redis.raw = record.model_dump_json()
    with pytest.raises(GitConnectPreviewError, match="expired"):
        await GitHubSyncService.load_connect_preview(
            "token", requested_by_user_id="owner", organization_id="org"
        )
    assert redis.deleted is True
