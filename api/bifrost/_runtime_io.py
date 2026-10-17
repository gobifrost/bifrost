"""Runtime telemetry over the authenticated shared SDK transport."""

from .client import get_client, raise_for_status_with_detail


def write_log(level: str, message: str) -> str | None:
    response = get_client().engine_request_sync(
        "POST", "/api/sdk/runtime/logs", json={"level": level, "message": message},
    )
    raise_for_status_with_detail(response)
    return response.json()["entry_id"]
