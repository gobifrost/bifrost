"""
Test script execution with logging to verify logs are captured properly.
"""
import base64
import pytest

from src.sdk.context import Caller, Organization
from src.services.execution.engine import ExecutionRequest, execute


@pytest.mark.asyncio
async def test_script_logging_captured():
    """Test that logging.info() calls in scripts are captured."""
    # Create a simple script that uses logging
    script_code = """
import logging

logging.info("Test log message 1")
logging.warning("Test warning message")
logging.error("Test error message")
logging.info("Test log message 2")
"""

    # Encode script as base64
    code_base64 = base64.b64encode(script_code.encode('utf-8')).decode('utf-8')

    # Create execution request
    request = ExecutionRequest(
        execution_id="test-script-logging",
        caller=Caller(
            user_id="test-user",
            email="test@example.com",
            name="Test User"
        ),
        organization=Organization(
            id="test-org",
            name="Test Org",
            is_active=True
        ),
        code=code_base64,
        name=None,  # Scripts don't have names in registry
        parameters={},
        transient=True
    )

    # Execute script
    result = await execute(request)

    # Verify execution succeeded
    assert result.status.value == "Success"

    # Verify logs were captured
    assert result.logs is not None
    assert len(result.logs) >= 4, f"Expected at least 4 log messages, got {len(result.logs)}: {result.logs}"

    # Check log contents (logs are dictionaries with 'message' field)
    log_messages = [log['message'] if isinstance(log, dict) else log for log in result.logs]
    assert any("Test log message 1" in msg for msg in log_messages), f"Missing log 1. Logs: {log_messages}"
    assert any("Test warning message" in msg for msg in log_messages), f"Missing warning. Logs: {log_messages}"
    assert any("Test error message" in msg for msg in log_messages), f"Missing error. Logs: {log_messages}"
    assert any("Test log message 2" in msg for msg in log_messages), f"Missing log 2. Logs: {log_messages}"


@pytest.mark.asyncio
async def test_script_with_variables_and_logging():
    """Test that both variables and logging work together in scripts."""
    script_code = """
import logging

x = 10
logging.info(f"Variable x = {x}")

y = 20
logging.info(f"Variable y = {y}")

result = x + y
logging.info(f"Result = {result}")
"""

    code_base64 = base64.b64encode(script_code.encode('utf-8')).decode('utf-8')

    request = ExecutionRequest(
        execution_id="test-script-vars-logs",
        caller=Caller(
            user_id="test-user",
            email="test@example.com",
            name="Test User"
        ),
        organization=Organization(
            id="test-org",
            name="Test Org",
            is_active=True
        ),
        code=code_base64,
        name=None,
        parameters={},
        transient=True
    )

    result = await execute(request)

    # Verify execution succeeded
    assert result.status.value == "Success"

    assert result.variables is not None
    assert "x" in result.variables
    assert result.variables["x"] == 10
    assert "y" in result.variables
    assert result.variables["y"] == 20
    assert "result" in result.variables
    assert result.variables["result"] == 30

    # Verify logs were captured (logging works even with coverage)
    assert result.logs is not None
    assert len(result.logs) >= 3, f"Expected at least 3 log messages, got {len(result.logs)}"

    log_messages = [log['message'] if isinstance(log, dict) else log for log in result.logs]
    assert any("Variable x = 10" in msg for msg in log_messages)
    assert any("Variable y = 20" in msg for msg in log_messages)
    assert any("Result = 30" in msg for msg in log_messages)
