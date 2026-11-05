"""Portable tests through python-keyring's real Windows adapter.

Also runnable with unittest on Windows, without API/server dependencies.
"""

import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import keyring

# Load the leaf SDK module without executing bifrost.__init__ (server SDK).
_spec = importlib.util.spec_from_file_location(
    "windows_credentials_under_test", Path(__file__).parents[2] / "bifrost/windows_credentials.py"
)
assert _spec is not None and _spec.loader is not None
_store_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store_module)
WindowsCredentialStore = _store_module.WindowsCredentialStore
CredentialStorageError = _store_module.CredentialStorageError


class WinError(Exception):
    def __init__(self, code, function):
        super().__init__("native diagnostic containing synthetic-secret")
        self.winerror = code
        self.funcname = function


class FakeWinCred:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_ENTERPRISE = 3

    def __init__(self):
        self.records = {}
        self.fail = None
        self.writes = []

    def CredRead(self, Type, TargetName):
        if TargetName not in self.records:
            raise WinError(1168, "CredRead")
        return dict(self.records[TargetName])

    def CredWrite(self, credential, flags):
        value = credential["CredentialBlob"].encode("utf-16-le")
        if len(value) > 2560:
            raise WinError(1783, "CredWrite")
        if self.fail:
            self.fail("write", credential["TargetName"])
        self.writes.append(len(value))
        self.records[credential["TargetName"]] = {**credential, "CredentialBlob": value}

    def CredDelete(self, Type, TargetName):
        if self.fail:
            self.fail("delete", TargetName)
        if TargetName not in self.records:
            raise WinError(1168, "CredDelete")
        del self.records[TargetName]


def adapter(native):
    parent = ModuleType("win32ctypes")
    child = ModuleType("win32ctypes.pywin32")
    child.pywintypes = SimpleNamespace(error=WinError)
    child.win32cred = native
    native.__name__ = "win32cred"
    parent.pywin32 = child
    spec = importlib.util.spec_from_file_location(
        "keyring.backends._bifrost_test_windows", Path(keyring.__file__).parent / "backends/Windows.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with (
        patch.dict("sys.modules", {"win32ctypes": parent, "win32ctypes.pywin32": child}),
        # Real backend classes register globally. Do not make our simulated
        # Windows adapter available to unrelated credential tests.
        patch.object(keyring.backend.KeyringBackend, "_classes", set()),
    ):
        spec.loader.exec_module(module)
    return module.WinVaultKeyring()


def record(url="https://one.example", token="synthetic-secret" * 160):
    return {"api_url": url, "access_token": token, "refresh_token": token, "expires_at": "2099-01-01T00:00:00+00:00"}


class TestWindowsCredentialStore(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.native = FakeWinCred()
        self.kr = adapter(self.native)
        self.store = WindowsCredentialStore(self.kr, Path(self.directory.name))

    def test_adapter_does_not_leak_into_global_backend_discovery(self):
        self.assertNotIn(type(self.kr), keyring.backend.KeyringBackend._classes)

    def test_wincred_limit_counts_utf16_bytes_including_surrogate_pairs(self):
        for valid, invalid in (("a" * 1280, "a" * 1281), ("🔐" * 640, "🔐" * 641)):
            self.kr.set_password("bifrost.boundary", "synthetic", valid)
            self.assertEqual(self.kr.get_password("bifrost.boundary", "synthetic"), valid)
            with self.assertRaises(WinError):
                self.kr.set_password("bifrost.boundary", "synthetic", invalid)
            self.assertEqual(self.kr.get_password("bifrost.boundary", "synthetic"), valid)

    def test_original_oversize_write_reproduces_native_failure(self):
        with self.assertRaises(WinError) as caught:
            self.kr.set_password("bifrost", "https://one.example", json.dumps(record()))
        self.assertEqual(caught.exception.winerror, 1783)

    def test_large_unicode_record_roundtrips_and_logout_removes_secret_fragments(self):
        value = record(token="🔐é" * 1200)
        self.store.save(value)
        self.assertEqual(self.store.get(value["api_url"]), value)
        self.assertEqual(self.store.list_urls(), [value["api_url"]])
        self.assertLessEqual(max(self.native.writes), 2560)
        self.store.clear(value["api_url"])
        self.assertIsNone(self.store.get(value["api_url"]))
        self.assertEqual(self.store.list_urls(), [])
        # The remaining snapshot contains only the logout tombstone.
        manifest = self.store._recover()
        self.assertEqual(self.store._load(manifest), {value["api_url"]: None})
        expected = {self.store.SERVICE, "current@" + self.store.SERVICE}
        expected.update(self.store._fragment_service(manifest, i) for i in range(manifest["count"]))
        self.assertLessEqual(set(self.native.records), expected)

    def test_legacy_profiles_remain_independent_during_migration(self):
        one, two = record(token="old-one"), record("https://two.example", "old-two")
        for value in (one, two):
            self.kr.set_password("bifrost", value["api_url"], json.dumps(value))
        self.kr.set_password("bifrost", "__index__", json.dumps([one["api_url"], two["api_url"]]))
        self.assertEqual(self.store.get(one["api_url"]), one)
        updated = record()
        self.store.save(updated)
        self.assertEqual(self.store.get(one["api_url"]), updated)
        self.assertEqual(self.store.get(two["api_url"]), two)
        self.assertEqual(self.store.list_urls(), sorted([one["api_url"], two["api_url"]]))
        self.assertIsNone(self.kr.get_password("bifrost", one["api_url"]))
        self.store.clear(two["api_url"])
        self.assertEqual(self.store.list_urls(), [one["api_url"]])
        self.assertIsNone(self.kr.get_password("bifrost", two["api_url"]))

    def test_interrupted_fragment_write_preserves_previous_and_recovers_orphans(self):
        old = record(token="previous-secret")
        self.store.save(old)
        existing = set(self.native.records)

        def fail(operation, target):
            if operation == "write" and target.startswith(self.store.SERVICE + ".") and target.endswith(".1"):
                raise WinError(1783, "CredWrite")

        self.native.fail = fail
        with self.assertRaisesRegex(CredentialStorageError, "fragment write") as caught:
            self.store.save(record())
        self.assertNotIn("synthetic-secret", str(caught.exception))
        self.native.fail = None
        self.assertEqual(self.store.get(old["api_url"]), old)
        def fragments(keys):
            return {key for key in keys if key.startswith(self.store.SERVICE + ".")}

        self.assertEqual(fragments(self.native.records), fragments(existing))
        self.assertIsNone(self.kr.get_password(self.store.SERVICE, "pending"))

    def test_failed_activation_preserves_previous_snapshot(self):
        old = record(token="previous-secret")
        self.store.save(old)

        original_write = self.native.CredWrite

        def write(credential, flags):
            if credential["UserName"] == "current" and credential["TargetName"] == self.store.SERVICE:
                raise WinError(1783, "CredWrite")
            original_write(credential, flags)

        with patch.object(self.native, "CredWrite", write):
            with self.assertRaisesRegex(CredentialStorageError, "activation"):
                self.store.save(record())
        self.assertEqual(self.store.get(old["api_url"]), old)

    def test_failed_cleanup_is_recoverable_after_activation(self):
        old = record(token="old")
        self.store.save(old)
        previous = self.store._recover()

        def fail(operation, target):
            if operation == "delete" and previous["generation"] in target:
                raise WinError(5, "CredDelete")

        self.native.fail = fail
        updated = record()
        with self.assertRaisesRegex(CredentialStorageError, "cleanup"):
            self.store.save(updated)
        self.native.fail = None
        self.assertEqual(self.store.get(updated["api_url"]), updated)
        self.assertFalse(any(previous["generation"] in key for key in self.native.records))

    def test_bad_readback_never_activates_partial_snapshot(self):
        old = record(token="old")
        self.store.save(old)
        original = self.kr.get_password

        def read(service, username):
            value = original(service, username)
            if username == "fragment" and service.endswith(".1"):
                return None
            return value

        with patch.object(self.kr, "get_password", read):
            with self.assertRaisesRegex(CredentialStorageError, "completeness"):
                self.store.save(record())
        self.assertEqual(self.store.get(old["api_url"]), old)

    def test_concurrent_saves_do_not_lose_profiles(self):
        barrier = threading.Barrier(2)
        errors = []

        def save(url):
            try:
                barrier.wait()
                WindowsCredentialStore(self.kr, Path(self.directory.name)).save(record(url))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=save, args=(f"https://{i}.example",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.store.list_urls(), ["https://0.example", "https://1.example"])
        for i in range(2):
            self.assertEqual(self.store.get(f"https://{i}.example"), record(f"https://{i}.example"))


if __name__ == "__main__":
    unittest.main()
