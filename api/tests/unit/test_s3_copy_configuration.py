"""Storage copy settings reach every Compose service that uses the bucket."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.config import Settings


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.debug.yml"])
def test_compose_passes_copy_mode_to_storage_consumers(filename):
    root = Path("/app") if Path("/app/docker-compose.yml").exists() else Path(__file__).resolve().parents[3]
    services = yaml.safe_load((root / filename).read_text())["services"]
    consumers = {
        name: service["environment"]
        for name, service in services.items()
        if "BIFROST_S3_BUCKET" in service.get("environment", {})
    }
    assert {"api", "worker"} <= consumers.keys()
    for name, environment in consumers.items():
        assert environment.get("BIFROST_S3_MULTIPART_COPY_MODE") == (
            "${BIFROST_S3_MULTIPART_COPY_MODE:-server}"
        ), name


def test_copy_mode_defaults_to_server(monkeypatch):
    monkeypatch.delenv("BIFROST_S3_MULTIPART_COPY_MODE", raising=False)
    assert Settings(_env_file=None).s3_multipart_copy_mode == "server"


@pytest.mark.parametrize("mode", ["server", "stream"])
def test_copy_mode_reads_environment(monkeypatch, mode):
    monkeypatch.setenv("BIFROST_S3_MULTIPART_COPY_MODE", mode)
    assert Settings(_env_file=None).s3_multipart_copy_mode == mode


def test_copy_mode_rejects_invalid_environment(monkeypatch):
    monkeypatch.setenv("BIFROST_S3_MULTIPART_COPY_MODE", "automatic")
    with pytest.raises(ValidationError, match="s3_multipart_copy_mode"):
        Settings(_env_file=None)
