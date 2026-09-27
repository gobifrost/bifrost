from __future__ import annotations

import pytest

from src.services.execution.requirements_setup_result import (
    FailedPackage,
    RequirementsInstallResult,
)


def test_requirements_install_result_json_roundtrip():
    result = RequirementsInstallResult(
        attempted=["ok-package", "bad-package"],
        installed=["ok-package"],
        failed=[FailedPackage(package="bad-package", error="compiler missing")],
        requirements_installed=1,
        requirements_total=2,
    )

    restored = RequirementsInstallResult.from_json_dict(result.to_json_dict())

    assert restored == result
    assert restored.ok is False


def test_requirements_install_result_rejects_missing_protocol_fields():
    with pytest.raises(ValueError, match="missing fields"):
        RequirementsInstallResult.from_json_dict(
            {
                "attempted": [],
                "installed": [],
                "failed": [],
                "requirements_installed": 0,
            }
        )


def test_requirements_install_result_rejects_invalid_field_types():
    with pytest.raises(ValueError, match="attempted"):
        RequirementsInstallResult.from_json_dict(
            {
                "attempted": "demo",
                "installed": [],
                "failed": [],
                "requirements_installed": 0,
                "requirements_total": 0,
            }
        )
