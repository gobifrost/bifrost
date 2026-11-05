from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _app(
    *,
    app_model: str = "standalone_v2",
    solution_id=None,
    repo_path: str | None = None,
    active_deployment_id=None,
    sdk_package_version: str | None = "1.2.3",
    sdk_fingerprint: str | None = None,
    sdk_contract_version: int | None = 7,
    sdk_binding_api_major: int | None = None,
    sdk_built_at: datetime | None = None,
):
    return SimpleNamespace(
        app_model=app_model,
        solution_id=solution_id,
        repo_path=repo_path,
        active_deployment_id=active_deployment_id,
        sdk_package_version=sdk_package_version,
        sdk_fingerprint=sdk_fingerprint,
        sdk_contract_version=sdk_contract_version,
        sdk_binding_api_major=sdk_binding_api_major,
        sdk_built_at=sdk_built_at,
    )


def test_sdk_status_matrix() -> None:
    from src.services.application_sdk_status import (
        CurrentApplicationSdkMetadata,
        application_sdk_status,
    )

    current = CurrentApplicationSdkMetadata(
        package_version="v1.2.3",
        fingerprint="current-fp",
        contract_version=7,
    )

    assert application_sdk_status(_app(app_model="inline_v1"), current) == "not_applicable"
    assert application_sdk_status(_app(sdk_fingerprint=None), current) == "unknown"
    assert (
        application_sdk_status(
            _app(sdk_fingerprint="older-fp", sdk_built_at=None),
            current,
        )
        == "unknown"
    )
    assert (
        application_sdk_status(
            _app(
                sdk_package_version=None,
                sdk_fingerprint="older-fp",
                sdk_built_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
            ),
            current,
        )
        == "unknown"
    )
    assert (
        application_sdk_status(
            _app(
                sdk_fingerprint="older-fp",
                sdk_contract_version=None,
                sdk_built_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
            ),
            current,
        )
        == "unknown"
    )
    assert (
        application_sdk_status(
            _app(
                sdk_fingerprint="current-fp",
                sdk_built_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
            ),
            current,
        )
        == "current"
    )
    assert (
        application_sdk_status(
            _app(
                sdk_fingerprint="older-fp",
                sdk_built_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
            ),
            current,
        )
        == "update_available"
    )


def test_legacy_compatible_sdk_can_opt_in_to_hosted_delivery_without_being_required() -> None:
    from src.services.application_sdk_status import CurrentApplicationSdkMetadata, application_sdk_status

    legacy = _app(
        sdk_fingerprint="bundled-sdk", sdk_contract_version=3,
        sdk_built_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
    )
    hosted = CurrentApplicationSdkMetadata(
        package_version="1.4.2", fingerprint="hosted-binding", contract_version=3,
        binding_api_major=1, supported_binding_api_majors=frozenset({1}),
    )
    assert application_sdk_status(legacy, hosted) == "update_available"


def test_sdk_status_is_unknown_when_current_fingerprint_is_missing() -> None:
    from src.services.application_sdk_status import (
        CurrentApplicationSdkMetadata,
        application_sdk_status,
    )

    current = CurrentApplicationSdkMetadata(
        package_version="v1.2.3",
        fingerprint=None,
        contract_version=7,
    )

    assert application_sdk_status(_app(sdk_fingerprint="app-fp"), current) == "unknown"


def test_hosted_binding_ignores_server_wire_and_implementation_changes() -> None:
    from src.services.application_sdk_status import CurrentApplicationSdkMetadata, application_sdk_status

    app = _app(
        sdk_fingerprint="binding-fp", sdk_contract_version=3,
        sdk_binding_api_major=1, sdk_built_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
    )
    for wire_version in (3, 4):
        target = CurrentApplicationSdkMetadata(
            package_version="2.0.0", fingerprint="binding-fp", contract_version=wire_version,
            binding_api_major=1, supported_binding_api_majors=frozenset({1}),
        )
        assert application_sdk_status(app, target) == "current"


def test_supported_sdk_major_does_not_require_silent_major_migration() -> None:
    from src.services.application_sdk_status import CurrentApplicationSdkMetadata, application_sdk_status

    app = _app(
        sdk_fingerprint="major-one-binding", sdk_binding_api_major=1,
        sdk_built_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
    )
    target = CurrentApplicationSdkMetadata(
        package_version="2.0.0", fingerprint="major-two-binding", contract_version=9,
        binding_api_major=2, supported_binding_api_majors=frozenset({1, 2}),
    )
    assert application_sdk_status(app, target) == "current"
    unsupported = CurrentApplicationSdkMetadata(
        package_version="3.0.0", fingerprint="major-two-binding", contract_version=10,
        binding_api_major=2, supported_binding_api_majors=frozenset({2}),
    )
    assert application_sdk_status(app, unsupported) == "update_required"


@pytest.mark.parametrize("contract_version", [2, 4])
@pytest.mark.parametrize("fingerprint", ["older-fp", "current-fp"])
def test_incompatible_sdk_contract_requires_update_even_if_fingerprint_matches(
    contract_version: int, fingerprint: str,
) -> None:
    from src.services.application_sdk_status import (
        CurrentApplicationSdkMetadata,
        application_sdk_status,
    )

    current = CurrentApplicationSdkMetadata(
        package_version="1.2.3", fingerprint="current-fp", contract_version=3,
    )
    app = _app(
        sdk_contract_version=contract_version, sdk_fingerprint=fingerprint,
        sdk_built_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
    )

    assert application_sdk_status(app, current) == "update_required"


async def test_load_current_sdk_metadata_degrades_when_build_toolchain_fails(monkeypatch) -> None:
    from src.services import application_sdk_status as status

    status.current_sdk_metadata.cache_clear()
    monkeypatch.setattr(status, "get_version", lambda: "v1.2-3-gabc1234")
    monkeypatch.setattr(status, "sdk_package_version", lambda version: "1.2.0")
    monkeypatch.setattr(
        status,
        "sdk_fingerprint",
        lambda version: (_ for _ in ()).throw(RuntimeError("node missing")),
    )
    monkeypatch.setattr(status, "sdk_contract_version", lambda: 7)

    try:
        metadata = await status.load_current_sdk_metadata()
    finally:
        status.current_sdk_metadata.cache_clear()

    assert metadata.package_version == "1.2.0"
    assert metadata.fingerprint is None
    assert metadata.contract_version is None


def test_sdk_source_available_is_cheap_capability_hint() -> None:
    from src.services.application_sdk_status import sdk_source_available

    built_at = datetime(2026, 9, 12, tzinfo=timezone.utc)

    assert sdk_source_available(_app(app_model="inline_v1")) is False
    assert sdk_source_available(_app(solution_id=uuid4(), repo_path="apps/example")) is True
    assert sdk_source_available(_app(solution_id=uuid4(), repo_path=None)) is False
    assert (
        sdk_source_available(
            _app(active_deployment_id=uuid4(), sdk_built_at=built_at)
        )
        is True
    )
    assert sdk_source_available(_app(active_deployment_id=uuid4(), sdk_built_at=None)) is False
    assert sdk_source_available(_app(active_deployment_id=None, sdk_built_at=built_at)) is False


def test_current_sdk_metadata_is_process_cached(monkeypatch) -> None:
    from src.services import application_sdk_status as status

    calls = {"get_version": 0, "package": 0, "fingerprint": 0, "contract": 0}

    def fake_get_version() -> str:
        calls["get_version"] += 1
        return "v9.9.9"

    def fake_fingerprint(version: str) -> str:
        calls["fingerprint"] += 1
        assert version == "v9.9.9"
        return "fp999"

    def fake_package_version(version: str) -> str:
        calls["package"] += 1
        assert version == "v9.9.9"
        return "9.9.9"

    def fake_contract() -> int:
        calls["contract"] += 1
        return 9

    status.current_sdk_metadata.cache_clear()
    monkeypatch.setattr(status, "get_version", fake_get_version)
    monkeypatch.setattr(status, "sdk_package_version", fake_package_version)
    monkeypatch.setattr(status, "sdk_fingerprint", fake_fingerprint)
    monkeypatch.setattr(status, "sdk_contract_version", fake_contract)

    first = status.current_sdk_metadata()
    second = status.current_sdk_metadata()

    assert first is second
    assert first.package_version == "9.9.9"
    assert first.fingerprint == "fp999"
    assert first.contract_version == 9
    assert calls == {"get_version": 1, "package": 1, "fingerprint": 1, "contract": 1}
    status.current_sdk_metadata.cache_clear()
