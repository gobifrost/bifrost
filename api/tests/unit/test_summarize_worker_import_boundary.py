"""Import-boundary regression tests for summarize/tune worker plumbing."""

import json
import subprocess
import sys


def test_summarize_worker_import_does_not_load_summary_or_tuning_services():
    code = """
import json
import sys

import src.jobs.summarize_worker  # noqa: F401

print(json.dumps({
    "run_summarizer_loaded": "src.services.execution.run_summarizer" in sys.modules,
    "tuning_service_loaded": "src.services.execution.tuning_service" in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {
        "run_summarizer_loaded": False,
        "tuning_service_loaded": False,
    }
