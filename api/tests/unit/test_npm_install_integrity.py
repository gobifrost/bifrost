"""Contracts for the checksum-verified global npm install in API images."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


def _find_api_file(path: str) -> Path:
    candidates = (
        Path("/app") / path.removeprefix("api/"),
        Path("/app") / path,
        Path(__file__).resolve().parents[3] / path,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"{path} not found")


INSTALLER = _find_api_file("api/scripts/install-npm.sh")
NPM_TARBALL_SHA512 = (
    "613ccbe90044257c87713bb426251ced128ca7e9b3949b65eeb18b98445a5d441"
    "e14720c73d6ad76be86f4cf42f2f0d66efe347a9a13400ccd2f6fcfb5954385"
)


def test_global_npm_installer_verifies_the_primary_registry_tarball_before_installing():
    installer = INSTALLER.read_text()

    assert 'NPM_VERSION="11.21.1"' in installer
    assert "https://registry.npmjs.org/npm/-/npm-${NPM_VERSION}.tgz" in installer
    assert NPM_TARBALL_SHA512 in installer
    assert "curl --fail --location --silent --show-error" in installer
    assert "--proto '=https'" in installer
    assert "sha512sum --check --status" in installer
    assert 'npm install --global --prefix /usr/local "$tarball" --no-audit --no-fund' in installer
    assert 'installed_version="$(/usr/local/bin/npm --version)"' in installer
    assert '[ "$installed_version" != "$NPM_VERSION" ]' in installer
    assert installer.index("sha512sum --check --status") < installer.index(
        'npm install --global --prefix /usr/local "$tarball"'
    )


@pytest.mark.parametrize("path", ["api/Dockerfile", "api/Dockerfile.dev"])
def test_api_images_use_the_same_verified_node_and_npm_toolchain(path):
    dockerfile = _find_api_file(path).read_text()
    assert (
        "FROM node:26.12.1-bookworm-slim@sha256:"
        "28147155d47dc31abe0a46e0d12b59b2fb3d0a2dcc5dc65fce76a074180b16fe "
        "AS node-toolchain"
    ) in dockerfile
    assert "libatomic1" in dockerfile
    assert "COPY --from=node-toolchain /usr/local/bin/node /usr/local/bin/node" in dockerfile
    assert "COPY --from=node-toolchain /usr/local/lib/node_modules/npm" in dockerfile
    assert "COPY api/scripts/install-npm.sh /usr/local/bin/install-npm.sh" in dockerfile
    assert "RUN sh /usr/local/bin/install-npm.sh" in dockerfile


def test_global_npm_installer_rejects_a_bad_checksum_before_running_npm(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm_called = tmp_path / "npm-called"

    (fake_bin / "curl").write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "    if [ \"$1\" = \"--output\" ]; then\n"
        "        printf corrupt > \"$2\"\n"
        "        exit 0\n"
        "    fi\n"
        "    shift\n"
        "done\n"
        "exit 2\n"
    )
    (fake_bin / "sha512sum").write_text("#!/bin/sh\nexit 1\n")
    (fake_bin / "npm").write_text('''#!/bin/sh\ntouch "$NPM_CALLED"\n''')
    for command in fake_bin.iterdir():
        command.chmod(0o755)

    result = subprocess.run(
        ["sh", str(INSTALLER)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "NPM_CALLED": str(npm_called),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not npm_called.exists()
