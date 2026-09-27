"""Lightweight result types for worker requirements setup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FailedPackage:
    """One requirements line that failed to install."""

    package: str
    error: str


@dataclass
class RequirementsInstallResult:
    """Outcome of a pool requirements install attempt."""

    attempted: list[str] = field(default_factory=list)
    installed: list[str] = field(default_factory=list)
    failed: list[FailedPackage] = field(default_factory=list)
    requirements_installed: int = 0
    requirements_total: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "installed": self.installed,
            "failed": [
                {"package": failed.package, "error": failed.error}
                for failed in self.failed
            ],
            "requirements_installed": self.requirements_installed,
            "requirements_total": self.requirements_total,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "RequirementsInstallResult":
        if not isinstance(data, dict):
            raise ValueError("requirements setup result must be an object")

        required_fields = {
            "attempted",
            "installed",
            "failed",
            "requirements_installed",
            "requirements_total",
        }
        missing = required_fields - data.keys()
        if missing:
            raise ValueError(
                f"requirements setup result missing fields: {', '.join(sorted(missing))}"
            )

        attempted = _expect_string_list(data["attempted"], "attempted")
        installed = _expect_string_list(data["installed"], "installed")
        failed_raw = data["failed"]
        if not isinstance(failed_raw, list):
            raise ValueError("requirements setup result field failed must be a list")
        failed: list[FailedPackage] = []
        for index, item in enumerate(failed_raw):
            if not isinstance(item, dict):
                raise ValueError(f"requirements setup result failed[{index}] must be an object")
            package = item.get("package")
            error = item.get("error")
            if not isinstance(package, str) or not isinstance(error, str):
                raise ValueError(
                    f"requirements setup result failed[{index}] package/error must be strings"
                )
            failed.append(FailedPackage(package=package, error=error))

        return cls(
            attempted=attempted,
            installed=installed,
            failed=failed,
            requirements_installed=_expect_int(
                data["requirements_installed"], "requirements_installed"
            ),
            requirements_total=_expect_int(data["requirements_total"], "requirements_total"),
        )


def _expect_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"requirements setup result field {field} must be a string list")
    return list(value)


def _expect_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"requirements setup result field {field} must be an integer")
    return value
