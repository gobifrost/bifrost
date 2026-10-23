"""Unit contracts for package-management router orchestration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from shared.subprocess_env import package_tool_env
from src.models import InstallPackageRequest
from src.routers.packages import (
    check_package_updates,
    check_updates,
    install_package,
    uninstall_package,
)


@pytest.mark.asyncio
async def test_package_updates_parse_pip_result_without_network() -> None:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(
        b'[{"name":"humanize","version":"4.12.0","latest_version":"4.13.0"}]',
        b"",
    ))
    proc.returncode = 0
    with patch(
        "src.routers.packages.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ) as create:
        updates = await check_package_updates()

    create.assert_awaited_once_with(
        "pip", "list", "--outdated", "--format=json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=package_tool_env(),
    )
    assert [(item.name, item.current_version, item.latest_version) for item in updates] == [
        ("humanize", "4.12.0", "4.13.0")
    ]


@pytest.mark.asyncio
async def test_package_updates_response_reports_parsed_count() -> None:
    with patch(
        "src.routers.packages.check_package_updates",
        new=AsyncMock(return_value=[
            {"name": "humanize", "current_version": "4.12.0", "latest_version": "4.13.0"}
        ]),
    ):
        response = await check_updates(MagicMock(), MagicMock())

    assert response.total_count == 1
    assert response.updates_available[0].name == "humanize"


@pytest.mark.asyncio
async def test_install_persists_requirements_before_broadcasting_recycle() -> None:
    """One request writes through S3/Redis before workers are notified.

    ``save_requirements`` owns the separately tested S3 + Redis write-through
    contract. This router test protects its ordering and message payload
    without starting a real pip mutation merely to inspect the same stores.
    """
    run_id = UUID("d2e87d6a-fb8e-4a64-a9b4-7eecc3fac49e")
    events: list[str] = []

    async def save(content: str, db: object) -> None:
        assert content == "requests==2.31.0\nhumanize==4.13.0\n"
        events.append("saved")

    async def publish(*, exchange_name: str, message: dict) -> None:
        assert exchange_name == "package-installations"
        assert message == {
            "type": "recycle_workers",
            "package": "humanize",
            "version": "4.13.0",
            "is_update": False,
            "run_id": str(run_id),
        }
        events.append("published")

    async def record(db: object, **change: object) -> None:
        assert change == {
            "action": "install",
            "package": "humanize",
            "version": "4.13.0",
            "run_id": str(run_id),
        }
        events.append("recorded")

    with (
        patch("src.routers.packages.record_package_change", new=record),
        patch(
            "src.routers.packages.get_requirements",
            new=AsyncMock(
                return_value={
                    "content": "requests==2.31.0\n",
                    "hash": "irrelevant",
                }
            ),
        ),
        patch("src.routers.packages.save_requirements", new=save),
        patch("src.routers.packages.publish_broadcast", new=publish),
        patch("src.routers.packages.uuid4", return_value=run_id),
    ):
        response = await install_package(
            InstallPackageRequest(package_name="humanize", version="4.13.0"),
            MagicMock(),
            MagicMock(),
        )

    assert events == ["saved", "recorded", "published"]
    assert response.status == "success"


@pytest.mark.asyncio
async def test_uninstall_records_the_change_with_the_broadcast_run_id() -> None:
    run_id = UUID("6c1f2f1e-27a4-4a8e-9d55-2f3a0f6d1c11")
    recorded: list[dict] = []
    published: list[dict] = []

    async def record(db: object, **change: object) -> None:
        recorded.append(change)

    async def publish(*, exchange_name: str, message: dict) -> None:
        published.append(message)

    with (
        patch("src.routers.packages.record_package_change", new=record),
        patch(
            "src.routers.packages.get_requirements",
            new=AsyncMock(return_value={"content": "humanize==4.13.0\n", "hash": "x"}),
        ),
        patch("src.routers.packages.save_requirements", new=AsyncMock()),
        patch("src.routers.packages.publish_broadcast", new=publish),
        patch("src.routers.packages.uuid4", return_value=run_id),
    ):
        await uninstall_package("humanize", MagicMock(), MagicMock())

    assert recorded == [
        {"action": "uninstall", "package": "humanize", "version": None, "run_id": str(run_id)}
    ]
    assert published[0]["run_id"] == str(run_id)
