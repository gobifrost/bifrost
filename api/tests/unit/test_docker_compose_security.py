"""Security invariants for the production Docker Compose stack."""

from pathlib import Path

import yaml


def _compose_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docker-compose.yml"
        if candidate.exists():
            return candidate

    raise FileNotFoundError("docker-compose.yml was not mounted for compose security tests")


def _load_compose() -> dict:
    with _compose_path().open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_seaweedfs_s3_api_is_not_published_to_host():
    """The bundled object store is internal-only in the production stack."""
    seaweedfs = _load_compose()["services"]["seaweedfs"]

    assert "ports" not in seaweedfs
    assert seaweedfs["expose"] == ["8333"]


def test_seaweedfs_uses_explicit_s3_identity_config():
    """SeaweedFS must not rely on unauthenticated S3 defaults."""
    seaweedfs = _load_compose()["services"]["seaweedfs"]
    command = seaweedfs["command"]

    assert seaweedfs["entrypoint"] == ["/bin/sh", "-ec"]
    assert "seaweedfs-s3.json" in command
    assert '"identities"' in command
    assert '"credentials"' in command
    assert "$${AWS_ACCESS_KEY_ID}" in command
    assert "$${AWS_SECRET_ACCESS_KEY}" in command
    assert "-s3.config=/tmp/seaweedfs-s3.json" in command
