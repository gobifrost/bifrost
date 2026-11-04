"""Disable retired distro roots without replacing administrator trust stores."""

from __future__ import annotations

import hashlib
from pathlib import Path
import ssl
import subprocess


RETIRED_ANCHORS = frozenset({
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b",
    "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4",
})


def disable_retired_roots(config: Path, certificates: Path) -> None:
    """Persist distro exclusions so later update-ca-certificates retains them."""
    lines = config.read_text().splitlines(keepends=True)
    for index, line in enumerate(lines):
        entry = line.strip()
        if not entry or entry.startswith(("#", "!")):
            continue
        certificate = certificates / entry
        der = ssl.PEM_cert_to_DER_cert(certificate.read_text())
        if hashlib.sha256(der).hexdigest() in RETIRED_ANCHORS:
            lines[index] = "!" + line
    config.write_text("".join(lines))


if __name__ == "__main__":
    disable_retired_roots(Path("/etc/ca-certificates.conf"), Path("/usr/share/ca-certificates"))
    subprocess.run(["update-ca-certificates", "--fresh"], check=True)
