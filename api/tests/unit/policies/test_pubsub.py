"""Pubsub publish_document_change payload shape tests."""

from unittest.mock import AsyncMock, patch

import pytest

from src.core.pubsub import (
    publish_document_change,
    publish_file_change,
    publish_file_policy_changed,
)


@pytest.mark.asyncio
async def test_publish_insert_carries_new_row_only():
    with patch("src.core.pubsub.publisher.publish", new=AsyncMock()) as mock_pub:
        await publish_document_change(
            table_id="00000000-0000-0000-0000-000000000001",
            action="insert",
            old_row=None,
            new_row={"id": "r1", "data": {"x": 1}},
        )
        args = mock_pub.await_args
        payload = args.kwargs.get("payload") or args.args[1]
        assert payload["action"] == "insert"
        assert payload["new_row"] == {"id": "r1", "data": {"x": 1}}
        assert payload.get("old_row") is None


@pytest.mark.asyncio
async def test_publish_update_carries_both():
    with patch("src.core.pubsub.publisher.publish", new=AsyncMock()) as mock_pub:
        await publish_document_change(
            table_id="00000000-0000-0000-0000-000000000001",
            action="update",
            old_row={"id": "r1", "data": {"x": 1}},
            new_row={"id": "r1", "data": {"x": 2}},
        )
        payload = mock_pub.await_args.kwargs.get("payload") or mock_pub.await_args.args[1]
        assert payload["old_row"]["data"]["x"] == 1
        assert payload["new_row"]["data"]["x"] == 2


@pytest.mark.asyncio
async def test_publish_delete_carries_old_row_only():
    with patch("src.core.pubsub.publisher.publish", new=AsyncMock()) as mock_pub:
        await publish_document_change(
            table_id="00000000-0000-0000-0000-000000000001",
            action="delete",
            old_row={"id": "r1", "data": {"x": 1}},
            new_row=None,
        )
        payload = mock_pub.await_args.kwargs.get("payload") or mock_pub.await_args.args[1]
        assert payload["action"] == "delete"
        assert payload["old_row"]["id"] == "r1"
        assert payload.get("new_row") is None


@pytest.mark.asyncio
async def test_publish_file_change_uses_scoped_channel():
    with patch("src.core.pubsub.publisher.publish", new=AsyncMock()) as mock_pub:
        await publish_file_change(
            location="shared",
            scope="00000000-0000-0000-0000-000000000001",
            path="gallery/a.png",
            action="write",
        )
        args = mock_pub.await_args
        assert args.args[0] == "files:shared:00000000-0000-0000-0000-000000000001"
        payload = args.kwargs["payload"]
        assert payload == {
            "type": "file_change",
            "location": "shared",
            "scope": "00000000-0000-0000-0000-000000000001",
            "path": "gallery/a.png",
            "action": "write",
        }


@pytest.mark.asyncio
async def test_publish_file_policy_changed_uses_global_workspace_channel():
    with patch("src.core.pubsub.publisher.publish", new=AsyncMock()) as mock_pub:
        await publish_file_policy_changed(
            location="workspace",
            scope=None,
            path="docs",
        )
        args = mock_pub.await_args
        assert args.args[0] == "files:workspace:GLOBAL"
        assert args.kwargs["payload"] == {
            "type": "file_policy_changed",
            "location": "workspace",
            "scope": None,
            "path": "docs",
        }
