"""Preserve locked app dependencies while replacing the instance SDK."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class ReproducibleDependencyBuildError(Exception):
    """The app dependency graph cannot be reproduced safely."""


def _output_tail(exc: subprocess.CalledProcessError) -> str:
    raw = exc.stderr or exc.output or b""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    return "\n".join(text.strip().splitlines()[-20:]) or "(no output)"


def refresh_sdk_lockfile(workdir: Path, *, npm: str, timeout: int, env: dict[str, str]) -> None:
    """Replace only the vendored SDK entry in a declared npm lockfile.

    The automatic updater replaces ``bifrost-sdk.tgz`` with the target SDK,
    so a lockfile from the prior deployment can retain its old tarball
    integrity. npm prepares the new SDK declaration, then this method grafts
    only that declaration and package entry into the retained lockfile.
    This leaves every non-SDK lock value pinned even when npm normalizes
    unrelated optional-package metadata while producing its candidate.
    """
    lock_path = workdir / "package-lock.json"
    if not lock_path.is_file():
        raise ReproducibleDependencyBuildError(
            "Locked builds require package-lock.json in the App source."
        )

    original_bytes = lock_path.read_bytes()
    try:
        original_lock = json.loads(original_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReproducibleDependencyBuildError(
            "Locked builds require a valid package-lock.json. Update the lockfile and redeploy."
        ) from exc

    original_packages, original_root = _lockfile_packages_and_root(original_lock)
    _validate_root_manifest(workdir, original_root)
    if _SDK_PACKAGE_KEY in original_packages:
        _validate_sdk_package_entry(original_packages[_SDK_PACKAGE_KEY])
    _validate_legacy_sdk_entry(original_lock)

    try:
        subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
            [
                npm,
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "bifrost@file:./bifrost-sdk.tgz",
            ],
            cwd=str(workdir),
            check=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        refreshed_lock = json.loads(lock_path.read_bytes())
    except subprocess.CalledProcessError as exc:
        lock_path.write_bytes(original_bytes)
        raise ReproducibleDependencyBuildError(
            "Could not refresh the retained SDK entry in package-lock.json: "
            f"{_output_tail(exc)}. Redeploy "
            "with a package-lock.json that can preserve the locked dependencies."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        lock_path.write_bytes(original_bytes)
        raise ReproducibleDependencyBuildError(
            "npm produced an invalid package-lock.json while preparing the SDK. "
            "Update the lockfile and redeploy."
        ) from exc

    try:
        refreshed_packages, refreshed_root = _lockfile_packages_and_root(
            refreshed_lock
        )
        refreshed_sdk = refreshed_packages.get(_SDK_PACKAGE_KEY)
        _validate_sdk_package_entry(refreshed_sdk)
        _validate_sdk_has_no_dependency_descendants(
            refreshed_packages, refreshed_sdk
        )
        _graft_sdk_lock_entries(
            original_lock,
            original_root,
            refreshed_lock,
            refreshed_root,
            refreshed_sdk,
        )
    except ReproducibleDependencyBuildError:
        lock_path.write_bytes(original_bytes)
        raise

    lock_path.write_text(json.dumps(original_lock, indent=2))


_ROOT_DEPENDENCY_SECTIONS = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)
_SDK_PACKAGE_KEY = "node_modules/bifrost"
_SDK_DESCENDANT_PREFIX = f"{_SDK_PACKAGE_KEY}/"
_SDK_DEPENDENCY_FIELDS = (
    "dependencies",
    "optionalDependencies",
    "bundledDependencies",
    "bundleDependencies",
)


def _lockfile_packages_and_root(lockfile: object) -> tuple[dict, dict]:
    if not isinstance(lockfile, dict):
        raise ReproducibleDependencyBuildError(
            "Locked builds require a package-lock.json with a packages root. "
            "Redeploy with a current package-lock.json."
        )
    packages = lockfile.get("packages")
    root = packages.get("") if isinstance(packages, dict) else None
    if not isinstance(packages, dict) or not isinstance(root, dict):
        raise ReproducibleDependencyBuildError(
            "Locked builds require a package-lock.json with a packages root. "
            "Redeploy with a current package-lock.json."
        )
    return packages, root


def _root_dependency_declarations(root: dict) -> dict[str, dict]:
    declarations: dict[str, dict] = {}
    for section in _ROOT_DEPENDENCY_SECTIONS:
        values = root.get(section)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ReproducibleDependencyBuildError(
                "Locked builds require valid root dependency declarations "
                "in package-lock.json. Redeploy with a "
                "current package-lock.json."
            )
        without_sdk = {
            name: value for name, value in values.items() if name != "bifrost"
        }
        if without_sdk:
            declarations[section] = without_sdk
    return declarations


def _validate_root_manifest(workdir: Path, lock_root: dict) -> None:
    package_path = workdir / "package.json"
    try:
        package_manifest = json.loads(package_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReproducibleDependencyBuildError(
            "Locked builds require a valid package.json matching the retained "
            "package-lock.json. Redeploy with matching "
            "package manifests."
        ) from exc
    if not isinstance(package_manifest, dict):
        raise ReproducibleDependencyBuildError(
            "Locked builds require a valid package.json matching the retained "
            "package-lock.json. Redeploy with matching "
            "package manifests."
        )
    if _root_dependency_declarations(package_manifest) != _root_dependency_declarations(
        lock_root
    ):
        raise ReproducibleDependencyBuildError(
            "Retained package.json dependency declarations do not match package-lock.json. "
            "Redeploy with matching package manifests."
        )


def _validate_sdk_package_entry(entry: object) -> None:
    if not isinstance(entry, dict) or not isinstance(entry.get("version"), str) or not isinstance(
        entry.get("resolved"), str
    ):
        raise ReproducibleDependencyBuildError(
            "Locked builds require a valid SDK package entry in package-lock.json. "
            "Redeploy with a current package-lock.json."
        )


def _validate_legacy_sdk_entry(lockfile: dict) -> None:
    legacy_dependencies = lockfile.get("dependencies")
    if isinstance(legacy_dependencies, dict) and "bifrost" in legacy_dependencies:
        _validate_sdk_package_entry(legacy_dependencies["bifrost"])


def _validate_sdk_has_no_dependency_descendants(
    packages: dict, sdk_entry: dict
) -> None:
    if any(path.startswith(_SDK_DESCENDANT_PREFIX) for path in packages):
        raise ReproducibleDependencyBuildError(
            "The refreshed SDK requires unsupported dependency descendants. "
            "Update the App dependency lockfile before building."
        )
    for field in _SDK_DEPENDENCY_FIELDS:
        value = sdk_entry.get(field)
        if value not in (None, {}, []):
            raise ReproducibleDependencyBuildError(
                "The refreshed SDK requires unsupported dependency descendants. "
                "Update the App dependency lockfile before building."
            )


def _graft_sdk_lock_entries(
    original_lock: dict,
    original_root: dict,
    refreshed_lock: dict,
    refreshed_root: dict,
    refreshed_sdk: dict,
) -> None:
    _root_dependency_declarations(refreshed_root)
    has_sdk_root_declaration = any(
        isinstance(refreshed_root.get(section), dict)
        and "bifrost" in refreshed_root[section]
        for section in _ROOT_DEPENDENCY_SECTIONS
    )
    if not has_sdk_root_declaration:
        raise ReproducibleDependencyBuildError(
            "Locked builds require a refreshed SDK root declaration in "
            "package-lock.json. Redeploy with a current "
            "package-lock.json."
        )

    for section in _ROOT_DEPENDENCY_SECTIONS:
        original_values = original_root.get(section)
        if isinstance(original_values, dict):
            original_values.pop("bifrost", None)
        refreshed_values = refreshed_root.get(section)
        if isinstance(refreshed_values, dict) and "bifrost" in refreshed_values:
            if not isinstance(original_values, dict):
                original_values = {}
                original_root[section] = original_values
            original_values["bifrost"] = refreshed_values["bifrost"]

    original_lock["packages"][_SDK_PACKAGE_KEY] = refreshed_sdk
    refreshed_legacy_dependencies = refreshed_lock.get("dependencies")
    original_legacy_dependencies = original_lock.get("dependencies")
    has_original_legacy_sdk = isinstance(
        original_legacy_dependencies, dict
    ) and "bifrost" in original_legacy_dependencies
    has_refreshed_legacy_sdk = isinstance(
        refreshed_legacy_dependencies, dict
    ) and "bifrost" in refreshed_legacy_dependencies
    if has_refreshed_legacy_sdk:
        _validate_sdk_package_entry(refreshed_legacy_dependencies["bifrost"])
        if not isinstance(original_legacy_dependencies, dict):
            original_legacy_dependencies = {}
            original_lock["dependencies"] = original_legacy_dependencies
        original_legacy_dependencies["bifrost"] = refreshed_legacy_dependencies[
            "bifrost"
        ]
    elif has_original_legacy_sdk:
        raise ReproducibleDependencyBuildError(
            "Locked builds require a refreshed SDK legacy package entry in "
            "package-lock.json. Redeploy with a current "
            "package-lock.json."
        )
