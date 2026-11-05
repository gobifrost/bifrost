"""Run synthetic credential persistence on a disposable Windows CI runner."""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from keyring.backends.Windows import WinVaultKeyring

spec = importlib.util.spec_from_file_location(
    "windows_credentials_smoke", Path(__file__).parents[1] / "api/bifrost/windows_credentials.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def store_for(directory, namespace):
    store = module.WindowsCredentialStore(WinVaultKeyring(), Path(directory))
    store.SERVICE = namespace
    store.LEGACY_SERVICE = namespace + ".legacy"
    return store


def record(url, token):
    return {"api_url": url, "access_token": token, "refresh_token": token, "expires_at": "2099-01-01T00:00:00+00:00"}


def main():
    if len(sys.argv) > 1:
        _, directory, namespace, url = sys.argv
        store_for(directory, namespace).save(record(url, "synthetic-child" * 300))
        return
    namespace = "bifrost.test.credentials." + uuid4().hex
    with tempfile.TemporaryDirectory() as directory:
        store = store_for(directory, namespace)
        url = "https://synthetic-one.invalid"
        other = "https://synthetic-two.invalid"
        try:
            legacy = record(url, "synthetic-legacy")
            store._kr.set_password(store.LEGACY_SERVICE, url, json.dumps(legacy))
            store._kr.set_password(store.LEGACY_SERVICE, "__index__", json.dumps([url]))
            assert store.get(url) == legacy
            large = record(url, "🔐é-synthetic" * 1000)
            store.save(large)
            assert store.get(url) == large
            assert store._kr.get_password(store.LEGACY_SERVICE, url) is None
            rotated = record(url, "synthetic-rotated" * 400)
            store.save(rotated)
            assert store.get(url) == rotated
            # Separate processes exercise the actual Windows file lock.
            processes = [subprocess.Popen([
                sys.executable, __file__, directory, namespace, target
            ]) for target in (url, other)]
            try:
                for process in processes:
                    assert process.wait(timeout=30) == 0
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            assert store.list_urls() == sorted([url, other])
            assert store.get(url) == record(url, "synthetic-child" * 300)
            assert store.get(other) == record(other, "synthetic-child" * 300)
            store.clear(url)
            assert store.get(url) is None
            assert store.get(other) is not None
            store.clear(other)
            assert store.list_urls() == []
        finally:
            # This namespace is unique to this run. Never touch real profiles.
            with store._locked():
                manifest = store._recover()
                store._remove_generation(manifest)
                store._delete(store.SERVICE, "current")
                store._delete(store.SERVICE, "pending")
                for username in (url, other, "__index__"):
                    store._delete(store.LEGACY_SERVICE, username)
    print("Windows native credential save/read/rotation/concurrency/logout passed.")


if __name__ == "__main__":
    main()
