"""One worker execution proves fresh transport imports retain their log boundary."""

import uuid

from tests.helpers.polling import poll_until


def test_inline_script_keeps_user_debug_without_fresh_transport_debug(
    e2e_client, platform_admin
):
    marker = uuid.uuid4().hex
    user_message = f"user-debug-{marker}"
    transport_message = f"transport-debug-{marker}"
    code = f'''
import logging
import sys

# Each worker child runs one execution. Force the first-import path even if
# the fork template inherited these modules from another platform import.
for module_name in tuple(sys.modules):
    if module_name.split(".")[0] in ("requests", "urllib3"):
        del sys.modules[module_name]

import requests
import urllib3.connectionpool

logging.getLogger("user").debug({user_message!r})
urllib3.connectionpool.log.debug({transport_message!r})
result = {{"ok": True}}
'''
    response = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={"code": code, "script_name": f"transport-{marker}.py"},
    )
    assert response.status_code == 200, response.text
    execution_id = response.json()["execution_id"]

    def completed():
        detail = e2e_client.get(
            f"/api/executions/{execution_id}", headers=platform_admin.headers
        )
        assert detail.status_code == 200, detail.text
        result = detail.json()
        return result if result["status"] in ("Success", "Failed") else None

    result = poll_until(completed, max_wait=30, interval=0.2)
    assert result is not None, f"Execution {execution_id} did not finish"
    assert result["status"] == "Success", result
    assert result["result"] == {"ok": True}

    logs = e2e_client.get(
        f"/api/executions/{execution_id}/logs", headers=platform_admin.headers
    )
    assert logs.status_code == 200, logs.text
    messages = [entry["message"] for entry in logs.json()]
    assert user_message in messages
    assert transport_message not in messages
