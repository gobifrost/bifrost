"""Compose installations can pin a Bifrost release for an upgrade.

The token releases must be taken one at a time (docs/runbooks/authz-epoch.md),
so every Bifrost image in docker-compose.yml follows one release variable.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_PINNED = {
    "init": "ghcr.io/gobifrost/bifrost-api:${BIFROST_VERSION:-latest}",
    "api": "ghcr.io/gobifrost/bifrost-api:${BIFROST_VERSION:-latest}",
    "worker": "ghcr.io/gobifrost/bifrost-api:${BIFROST_VERSION:-latest}",
    "client": "ghcr.io/gobifrost/bifrost-client:${BIFROST_VERSION:-latest}",
}


def _compose_file() -> Path:
    for candidate in (Path("/app/docker-compose.yml"), Path(__file__).resolve().parents[3] / "docker-compose.yml"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("docker-compose.yml is not mounted")


def test_every_bifrost_image_follows_the_release_variable() -> None:
    services = yaml.safe_load(_compose_file().read_text())["services"]
    bifrost_images = {
        name: service["image"]
        for name, service in services.items()
        if "ghcr.io/gobifrost/" in service.get("image", "")
    }

    assert bifrost_images == _PINNED
