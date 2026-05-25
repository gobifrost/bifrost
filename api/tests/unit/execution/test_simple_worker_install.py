"""Unit tests for install_requirements resilient install + result reporting."""
from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

from src.services.execution.simple_worker import (
    install_requirements,
    RequirementsInstallResult,
)


def _completed(returncode: int, stderr: str = "", stdout: str = "") -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stderr = stderr
    m.stdout = stdout
    return m


def test_no_requirements_returns_empty_result():
    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=None
    ):
        result = install_requirements()
    assert isinstance(result, RequirementsInstallResult)
    assert result.attempted == []
    assert result.failed == []
    assert result.ok is True


def test_batch_success_marks_all_installed():
    content = "anthropic\nlitellm\nreportlab\n"
    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=content
    ), patch(
        "src.services.execution.simple_worker.subprocess.run",
        return_value=_completed(0),
    ) as run:
        result = install_requirements()
    assert run.call_count == 1
    assert result.ok is True
    assert set(result.installed) == {"anthropic", "litellm", "reportlab"}
    assert result.failed == []


def test_batch_failure_falls_back_to_per_package_and_isolates_bad_dep():
    content = "anthropic\nxhtml2pdf\nreportlab\n"

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "-r" in cmd:
            return _completed(1, stderr="metadata-generation-failed: pycairo")
        if "xhtml2pdf" in joined:
            return _completed(1, stderr="ERROR: Unknown compiler(s): cc gcc")
        return _completed(0)

    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=content
    ), patch(
        "src.services.execution.simple_worker.subprocess.run", side_effect=fake_run
    ):
        result = install_requirements()

    assert result.ok is False
    assert set(result.installed) == {"anthropic", "reportlab"}
    assert [f.package for f in result.failed] == ["xhtml2pdf"]
    assert "Unknown compiler" in result.failed[0].error


def test_comments_and_blank_lines_are_ignored():
    content = "# a comment\n\nanthropic\n  \n# another\nlitellm\n"
    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=content
    ), patch(
        "src.services.execution.simple_worker.subprocess.run",
        return_value=_completed(0),
    ):
        result = install_requirements()
    assert set(result.attempted) == {"anthropic", "litellm"}


def test_per_package_fallback_reapplies_pip_options():
    content = "--extra-index-url https://example.com/simple\nanthropic\nlitellm\n"

    per_package_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        if "-r" in cmd:
            # Force batch failure so we reach the per-package fallback.
            return _completed(1, stderr="batch boom")
        per_package_calls.append(list(cmd))
        return _completed(0)

    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=content
    ), patch(
        "src.services.execution.simple_worker.subprocess.run", side_effect=fake_run
    ):
        result = install_requirements()

    # Option line is not treated as a package.
    assert set(result.attempted) == {"anthropic", "litellm"}
    assert set(result.installed) == {"anthropic", "litellm"}
    assert result.ok is True

    # Every per-package invocation carries the index config.
    assert len(per_package_calls) == 2
    for cmd in per_package_calls:
        assert "--extra-index-url" in cmd
        assert "https://example.com/simple" in cmd


def test_per_package_timeout_isolates_failed_package():
    content = "anthropic\nslowpkg\nlitellm\n"

    def fake_run(cmd, **kwargs):
        if "-r" in cmd:
            # Force batch failure so we reach the per-package fallback.
            return _completed(1, stderr="batch boom")
        if "slowpkg" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=300)
        return _completed(0)

    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=content
    ), patch(
        "src.services.execution.simple_worker.subprocess.run", side_effect=fake_run
    ):
        result = install_requirements()

    assert result.ok is False
    assert set(result.installed) == {"anthropic", "litellm"}
    assert [f.package for f in result.failed] == ["slowpkg"]
    assert result.failed[0].error == "pip install timed out (300s)"
