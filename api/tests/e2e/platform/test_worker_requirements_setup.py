"""Real requirements helper installs a local wheel into an isolated user site."""

import json
import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from zipfile import ZipFile

import pytest
import redis

from src.config import get_settings
from src.core.module_cache_sync import _get_s3_client
from src.services.execution.requirements_setup_result import RequirementsInstallResult


@pytest.mark.e2e
def test_requirements_helper_installs_package_visible_to_fresh_python(tmp_path, monkeypatch):
    package = f"bifrost_setup_probe_{uuid4().hex}"
    distribution = package.replace("_", "-")
    requirement = f"{distribution}==0.0.1"
    wheel = tmp_path / f"{package}-0.0.1-py3-none-any.whl"
    metadata = f"{package}-0.0.1.dist-info"
    files = {
        f"{package}/__init__.py": 'VALUE = "installed-by-helper"\n',
        f"{metadata}/METADATA": f"Metadata-Version: 2.1\nName: {distribution}\nVersion: 0.0.1\n",
        f"{metadata}/WHEEL": "Wheel-Version: 1.0\nGenerator: bifrost-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    with ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr(f"{metadata}/RECORD", "".join(f"{name},,\n" for name in files))

    # Logical DB 15 keeps this fixture out of the running worker's requirements.
    url = urlsplit(get_settings().redis_url)
    isolated_url = urlunsplit(url._replace(path="/15"))
    client = redis.Redis.from_url(isolated_url)
    key = "bifrost:requirements:content"
    storage = _get_s3_client()
    assert storage is not None
    bucket = os.environ["BIFROST_S3_BUCKET"]
    object_key = "_repo/requirements.txt"
    try:
        original_object = storage.get_object(Bucket=bucket, Key=object_key)["Body"].read()
    except storage.exceptions.NoSuchKey:
        original_object = None
    original = client.get(key)
    original_ttl = client.pttl(key)
    try:
        storage.put_object(Bucket=bucket, Key=object_key, Body=requirement.encode())
        client.delete(key)
        monkeypatch.setenv("BIFROST_REDIS_URL", isolated_url)
        monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path / "user-site"))
        monkeypatch.setenv("PIP_USER", "1")
        monkeypatch.setenv("PIP_NO_INDEX", "1")
        monkeypatch.setenv("PIP_FIND_LINKS", str(tmp_path))
        monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")

        parent = subprocess.run(
            [sys.executable, "-c", """
import asyncio, json, sys
from src.services.execution.process_pool import _run_requirements_setup_subprocess
result = asyncio.run(_run_requirements_setup_subprocess())
print(json.dumps({
    "result": result.to_json_dict(),
    "retained_setup_modules": [name for name in (
        "src.core.requirements_cache", "src.services.repo_storage",
        "botocore", "aiobotocore",
    ) if name in sys.modules],
}))
"""],
            env=os.environ.copy(), capture_output=True, text=True, timeout=60, check=True,
        )
        payload = json.loads(parent.stdout)
        assert payload["retained_setup_modules"] == []
        result = RequirementsInstallResult.from_json_dict(payload["result"])

        assert result.ok
        assert result.installed == [requirement]
        assert result.requirements_total == 1
        assert result.requirements_installed == 1
        probe = subprocess.run(
            [sys.executable, "-c", f"import {package}; print({package}.VALUE)"],
            env=os.environ.copy(), capture_output=True, text=True, timeout=20, check=True,
        )
        assert probe.stdout.strip() == "installed-by-helper"
    finally:
        if original_object is None:
            storage.delete_object(Bucket=bucket, Key=object_key)
        else:
            storage.put_object(Bucket=bucket, Key=object_key, Body=original_object)
        if original is None:
            client.delete(key)
        elif original_ttl > 0:
            client.set(key, original, px=original_ttl)
        else:
            client.set(key, original)
        client.close()
