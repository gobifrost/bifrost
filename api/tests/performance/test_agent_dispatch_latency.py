"""Compare first and subsequent real agent/summary dispatches explicitly.

Each cycle enqueues one agent and its automatic summary against the local
provider. Run on the baseline and candidate stacks with the memory probe.
"""

import httpx
import pytest

from tests.e2e.conftest import E2E_API_URL
from tests.e2e.platform.test_agent_runtime_boundary import (
    test_api_agent_run_and_automatic_summary_use_real_dispatch_process as _run_agent,
)

pytestmark = pytest.mark.slow


@pytest.fixture(scope="session")
def e2e_client():
    with httpx.Client(base_url=E2E_API_URL, timeout=60) as client:
        yield client


def test_first_and_subsequent_agent_dispatch(e2e_client, platform_admin, async_session_factory):
    # One cold cycle plus five warm samples: compare a distribution rather
    # than treating a single scheduler wake-up as a latency result.
    for cycle in range(6):
        print(f"AGENT_DISPATCH_CYCLE {cycle + 1}")
        _run_agent(e2e_client, platform_admin, async_session_factory)
