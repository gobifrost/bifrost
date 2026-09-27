import pytest
from click import ClickException

from bifrost.platform_jobs import poll_platform_job


class _Response:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, object]:
        return {
            "status": "requires_action",
            "result": {"requires_action": "confirm_deletes"},
        }


class _Client:
    async def get(self, _path: str) -> _Response:
        return _Response()


@pytest.mark.asyncio
async def test_poll_platform_job_stops_for_required_action() -> None:
    with pytest.raises(ClickException, match="requires action") as exc_info:
        await poll_platform_job(
            _Client(),
            "job-1",
            label="Syncing workspace",
            timeout_seconds=0,
        )

    assert "confirm_deletes" in exc_info.value.message
