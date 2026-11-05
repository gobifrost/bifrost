"""Versioned build identities shared by the CLI and the server builder.

Only hashes cross the wire; source and process configuration stay local.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import subprocess
import tarfile
from pathlib import PurePosixPath
from typing import Any, Mapping

SCHEMA = 1


def digest_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_identity(files: Mapping[str, bytes]) -> str:
    entries = []
    for path, content in sorted(files.items()):
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts or str(parsed) != path:
            raise ValueError("Invalid build input path")
        entries.append((path, hashlib.sha256(content).hexdigest()))
    return digest_json(entries)


def sdk_identity(content: bytes) -> str:
    """Hash exact package files, independent of gzip timestamps/order."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            if member.name in files:
                raise ValueError("Duplicate SDK package member")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Unreadable SDK package member")
            files[member.name] = stream.read()
    if not files:
        raise ValueError("Empty SDK package")
    return file_identity(files)


def build_recipe(
    sdk: bytes, *, variant: str, environment: Mapping[str, str]
) -> dict[str, Any]:
    # Hash the complete environment actually passed to the build. This includes
    # arbitrary build-plugin inputs without serializing any environment values.
    versions = {}
    for tool in ("node", "npm"):
        versions[tool] = subprocess.run(
            [tool, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=dict(environment),
        ).stdout.strip()
    npm_config = subprocess.run(
        ["npm", "config", "list", "--json", "--location=global"],
        check=True,
        capture_output=True,
        timeout=30,
        env=dict(environment),
    ).stdout
    return {
        "schema": SCHEMA,
        "variant": variant,
        "sdk": sdk_identity(sdk),
        "toolchain": digest_json(
            {
                **versions,
                "os": platform.system(),
                "arch": platform.machine(),
                "npm_config": hashlib.sha256(npm_config).hexdigest(),
            }
        ),
        "environment": digest_json(dict(environment)),
    }


def valid_recipe(recipe: object) -> bool:
    return (
        isinstance(recipe, dict)
        and set(recipe) == {"schema", "variant", "sdk", "toolchain", "environment"}
        and type(recipe["schema"]) is int
        and recipe["schema"] == SCHEMA
        and isinstance(recipe["variant"], str)
        and recipe["variant"]
        in {"cli-install-v1", "server-install-v1", "server-locked-v1"}
        and all(
            isinstance(recipe[k], str)
            and len(recipe[k]) == 64
            and all(c in "0123456789abcdef" for c in recipe[k])
            for k in ("sdk", "toolchain", "environment")
        )
    )


def build_identity(
    app_id: object,
    files: Mapping[str, bytes],
    dependencies: Mapping[str, str],
    recipe: dict[str, Any],
    *,
    package_name: str = "bifrost-app",
) -> str:
    if not valid_recipe(recipe):
        raise ValueError("Unknown build recipe")
    return digest_json(
        {
            "schema": SCHEMA,
            "app": str(app_id),
            "base": f"/api/applications/{app_id}/dist/",
            "source": file_identity(files),
            "dependencies": dict(dependencies),
            "default_package_name": package_name
            if "package.json" not in files
            else None,
            "recipe": recipe,
            "commands": [
                "npm ci"
                if recipe["variant"] == "server-locked-v1"
                else "npm install --no-audit --no-fund --package-lock=false",
                "npx vite build --base",
            ],
        }
    )
