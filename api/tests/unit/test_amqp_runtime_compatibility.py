"""The installed AMQP client must import during API startup."""

import subprocess
import sys


def test_amqp_client_imports_and_marks_connect_as_coroutine():
    code = """
import inspect
import aio_pika
import aiormq
assert inspect.iscoroutinefunction(aiormq.connect)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
