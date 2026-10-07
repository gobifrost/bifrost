"""Contracts for the checksum-verified global npm install in API images."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


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
    "e84875bb943e908557780f1eee5d9cfc7a67145730ae4b77ef10ccba30f96ded"
    "6096859af69ea3dc5b2fde60725d79aa247cbed9c12544c30bf28a4d4fbc4825"
)


def test_global_npm_installer_verifies_the_primary_registry_tarball_before_installing():
    installer = INSTALLER.read_text()

    assert 'NPM_VERSION="10.9.3"' in installer
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


def test_development_api_image_uses_the_checksum_verified_global_npm_installer():
    dockerfile = _find_api_file("api/Dockerfile.dev").read_text()
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
