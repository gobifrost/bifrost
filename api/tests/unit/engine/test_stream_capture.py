"""Printed workflow output must share the engine's redaction/logging boundary."""
import base64
import json
import logging
import sys
from unittest.mock import Mock

import pytest

from src.models.enums import ExecutionStatus
from src.sdk.context import Caller
from src.services.execution import engine

TOKEN = 'synthetic-print-credential-0123456789'


@pytest.mark.parametrize('fails', [False, True], ids=['success', 'failure'])
async def test_workflow_prints_are_returned_and_sent_to_existing_log_transport(monkeypatch, fails):
    delivered = Mock()
    monkeypatch.setattr(engine, 'write_log', delivered)

    async def workflow():
        print('stdout credential=' + TOKEN)
        print('stderr credential=' + TOKEN, file=sys.stderr)
        if fails:
            raise ValueError('synthetic stream failure')
        return 70

    request = engine.ExecutionRequest(execution_id='11111111-1111-4111-8111-111111111111',
        caller=Caller('22222222-2222-4222-8222-222222222222', 'print@example.invalid', 'Print'),
        organization=None, func=workflow, engine_token=TOKEN, no_cache=True, public_url='https://platform.invalid')
    result = await engine.execute(request)
    assert result.status == (ExecutionStatus.FAILED if fails else ExecutionStatus.SUCCESS)
    messages = [row['message'] for row in result.logs]
    assert messages.count('stdout credential=[REDACTED]') == 1
    assert messages.count('stderr credential=[REDACTED]') == 1
    assert TOKEN not in json.dumps(result.logs)
    delivered.assert_any_call(level='INFO', message='stdout credential=[REDACTED]')
    delivered.assert_any_call(level='ERROR', message='stderr credential=[REDACTED]')
    assert TOKEN not in repr(delivered.call_args_list)


async def test_inline_script_partial_line_is_captured_without_parsing_level_prefix(monkeypatch):
    delivered = Mock()
    monkeypatch.setattr(engine, 'write_log', delivered)
    code = base64.b64encode(b"print('[ERROR] literal text', end=''); result = 7").decode()
    request = engine.ExecutionRequest(execution_id='11111111-1111-4111-8111-111111111111',
        caller=Caller('22222222-2222-4222-8222-222222222222', 'print@example.invalid', 'Print'),
        organization=None, code=code, no_cache=True, public_url='https://platform.invalid')
    result = await engine.execute(request)
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == 7
    rows = [row for row in result.logs if row['message'] == '[ERROR] literal text']
    assert len(rows) == 1
    assert rows[0]['level'] == 'info'
    assert rows[0]['source'] == 'script'
    delivered.assert_any_call(level='INFO', message='[ERROR] literal text')


async def test_log_transport_failure_does_not_replace_success_or_leak_credentials(monkeypatch):
    monkeypatch.setattr(engine, 'write_log', Mock(side_effect=RuntimeError(TOKEN)))

    async def workflow():
        print('workflow output')
        return 70

    request = engine.ExecutionRequest(execution_id='11111111-1111-4111-8111-111111111111',
        caller=Caller('22222222-2222-4222-8222-222222222222', 'print@example.invalid', 'Print'),
        organization=None, func=workflow, engine_token=TOKEN, no_cache=True, public_url='https://platform.invalid')
    result = await engine.execute(request)
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == 70
    assert 'workflow output' in [row['message'] for row in result.logs]
    assert TOKEN not in json.dumps(result.logs)


async def test_structured_logging_still_uses_existing_capture_once(monkeypatch):
    monkeypatch.setattr(engine, 'write_log', Mock())

    async def workflow():
        logging.getLogger(__name__).warning('structured marker')
        return 70

    request = engine.ExecutionRequest(execution_id='11111111-1111-4111-8111-111111111111',
        caller=Caller('22222222-2222-4222-8222-222222222222', 'print@example.invalid', 'Print'),
        organization=None, func=workflow, no_cache=True, public_url='https://platform.invalid')
    result = await engine.execute(request)
    assert result.status == ExecutionStatus.SUCCESS
    assert [row['message'] for row in result.logs].count('structured marker') == 1
