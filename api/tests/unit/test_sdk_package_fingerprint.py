"""The SDK staleness gate hinges on two independent stamps:

- a content fingerprint (sha256 of the built bundle) that changes automatically
  whenever the shipped SDK source changes -- present both in the tarball's
  package.json and in GET /api/version.
- a contract version (sdk-contract.json) that only changes on a DECIDED
  breaking SDK<->server change -- same two-tier model as the CLI's
  CONTRACT_VERSION + DTO-fingerprint pair (see api/bifrost/contract_version.py).

Same source -> same fingerprint, stable across calls, and a broken node
toolchain must degrade gracefully rather than 500 /api/version.
"""

import io
import json
import tarfile

import pytest
import src.services.sdk_package as sdkpkg


def test_tarball_package_json_carries_fingerprint():
    data = sdkpkg.build_sdk_tarball("v1.2.3")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        pkg = json.load(tar.extractfile("package/package.json"))

    assert pkg["bifrost"]["fingerprint"] == sdkpkg.sdk_fingerprint("v1.2.3")
    assert len(pkg["bifrost"]["fingerprint"]) == 16


def test_fingerprint_is_stable_across_calls():
    assert sdkpkg.sdk_fingerprint("v1.2.3") == sdkpkg.sdk_fingerprint("v1.2.3")


def test_sdk_contract_version_is_positive_int():
    version = sdkpkg.sdk_contract_version()
    assert isinstance(version, int)
    assert version > 0


def test_sdk_contract_version_matches_json_file():
    contract = json.loads((sdkpkg._SDK_SRC / "sdk-contract.json").read_text())
    assert sdkpkg.sdk_contract_version() == contract["version"]


def test_tarball_package_json_carries_contract_version():
    data = sdkpkg.build_sdk_tarball("v1.2.3")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        pkg = json.load(tar.extractfile("package/package.json"))

    assert pkg["bifrost"]["contract"] == sdkpkg.sdk_contract_version()


def test_version_endpoint_reports_sdk_fingerprint_and_contract(monkeypatch):
    import asyncio

    from src.routers import version as version_router

    monkeypatch.setattr(version_router, "get_sdk_fingerprint", lambda: "abcd1234abcd1234")
    monkeypatch.setattr(version_router, "sdk_contract_version", lambda: 1)

    resp = asyncio.run(version_router.get_version_info())
    assert resp.sdk_fingerprint == "abcd1234abcd1234"
    assert resp.sdk_contract_version == 1
    assert "sdk_fingerprint" in version_router.VersionResponse.model_fields
    assert "sdk_contract_version" in version_router.VersionResponse.model_fields


def test_version_endpoint_builds_fingerprint_off_event_loop(monkeypatch):
    import asyncio

    from src.routers import version as version_router

    calls = []

    async def _fake_to_thread(func, *args):
        calls.append((func, args))
        return func(*args)

    monkeypatch.setattr(version_router.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(version_router, "get_sdk_fingerprint", lambda: "threaded-fp")
    monkeypatch.setattr(version_router, "sdk_contract_version", lambda: 1)

    resp = asyncio.run(version_router.get_version_info())
    assert resp.sdk_fingerprint == "threaded-fp"
    assert calls == [(version_router.get_sdk_fingerprint, ())]


def test_get_sdk_fingerprint_degrades_gracefully_on_build_failure(monkeypatch):
    """A broken node toolchain must not take down /api/version."""
    from src.routers import version as version_router

    def _boom(_version: str) -> str:
        raise RuntimeError("esbuild exploded")

    monkeypatch.setattr(version_router, "sdk_fingerprint", _boom)
    assert version_router.get_sdk_fingerprint() == "unavailable"


def test_version_endpoint_tolerates_fingerprint_failure(monkeypatch):
    import asyncio

    from src.routers import version as version_router

    def _boom() -> str:
        raise RuntimeError("esbuild exploded")

    monkeypatch.setattr(version_router, "get_sdk_fingerprint", _boom)
    with pytest.raises(RuntimeError):
        # get_sdk_fingerprint itself is the guarded call site; if the handler
        # calls it directly (not wrapped again), a raise here is a bug in the
        # handler -- this test documents that get_sdk_fingerprint is where the
        # try/except must live, not a second layer in the handler.
        asyncio.run(version_router.get_version_info())


def test_sdk_fingerprint_does_not_cache_failures(monkeypatch):
    """A transient node hiccup must be retryable on the next call, not
    permanently cached as a failure."""
    calls = {"n": 0}

    def _flaky(_version: str) -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure")
        return b"//bundle-ok"

    monkeypatch.setattr(sdkpkg, "_built_bundle", _flaky)

    with pytest.raises(RuntimeError):
        sdkpkg.sdk_fingerprint("v9.9.9-fingerprint-cache-test")

    # Second call retries the underlying build rather than replaying a cached
    # exception.
    result = sdkpkg.sdk_fingerprint("v9.9.9-fingerprint-cache-test")
    assert isinstance(result, str)
    assert calls["n"] == 2
