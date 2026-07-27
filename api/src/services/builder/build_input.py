"""Catalog-validated build input materialization for the private Solution
builder's server-side app compile step.

An AI-generated app's ``dependencies`` dict comes from whatever the agent
turn declared — it is untrusted input to the toolchain. Two gates apply
before it ever reaches ``npm install``:

1. :func:`validate_dependencies` — every package must be an EXACT match
   (name + version) against :data:`builder_package_catalog.json`. No semver
   ranges are honored in this first release: the catalog is small and
   hand-curated, so "close enough" isn't a case worth building yet.
2. :func:`materialize_build_input` strips/overrides anything in the app's
   own source that could hijack the build (a user-supplied
   ``vite.config.*``, ``.npmrc``, or npm lifecycle scripts) — Bifrost, not
   the generated app, controls how the toolchain is invoked.

This module is the extraction target for what used to live inline in
``SolutionAppBuilder._materialize`` (api/src/services/solutions/app_build.py);
that method is now a thin call into :func:`materialize_build_input`.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

# Same fixed DOS timestamp scaffold.py's zip_workspace uses — zip date fields
# start at 1980, so this is the earliest value that round-trips, and matching
# it means every deterministic zip in the builder uses one convention.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

_CATALOG_PATH = Path(__file__).resolve().parents[3] / "shared" / "builder_package_catalog.json"

# The app resolves `import ... from "bifrost"` against this local tarball —
# same mechanism/name SolutionAppBuilder already vendors under.
_SDK_TARBALL = "bifrost-sdk.tgz"

# Lifecycle/npm-script keys capable of running arbitrary code during
# `npm install`. We strip the whole `scripts` key (see materialize_build_input
# docstring) rather than denylist these individually, but the names are kept
# here for the exception message / clarity of intent.
_DANGEROUS_SCRIPT_KEYS = {"preinstall", "postinstall", "prepare", "prepublish", "install"}

_VITE_CONFIG_NAMES = {
    "vite.config.js",
    "vite.config.ts",
    "vite.config.mjs",
    "vite.config.cjs",
}

_BIFROST_VITE_CONFIG = """\
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Bifrost-owned Vite config for server-side app builds. `base` is
// intentionally NOT set here — the builder passes it on the CLI
// (`vite build --base <base>`) so the serving route stays a build-time
// concern, not something app source can override.
export default defineConfig({
  plugins: [react()],
});
"""

_BIFROST_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""


class UnsupportedDependency(Exception):
    """Raised when a caller-declared dependency isn't an exact catalog match.

    ``offenders`` maps each rejected package name to the version the caller
    requested — for both a package absent from the catalog entirely and a
    catalog package pinned to the wrong version. The two cases aren't
    distinguished in the dict itself; the exception message enumerates which
    is which.
    """

    def __init__(self, offenders: dict[str, str]):
        self.offenders = offenders
        detail = ", ".join(f"{name}@{version}" for name, version in sorted(offenders.items()))
        super().__init__(f"Unsupported build dependencies: {detail}")


def load_package_catalog() -> dict[str, str]:
    """Parse ``api/shared/builder_package_catalog.json`` into {name: exact_version}."""
    return json.loads(_CATALOG_PATH.read_text())


def validate_dependencies(dependencies: dict[str, str]) -> None:
    """Raise :class:`UnsupportedDependency` for any package not in the catalog
    or pinned to a version other than the catalog's exact pin.

    Exact-match only — no semver ranges in this first release.
    """
    catalog = load_package_catalog()
    offenders = {
        name: version
        for name, version in dependencies.items()
        if catalog.get(name) != version
    }
    if offenders:
        raise UnsupportedDependency(offenders)


def _sanitize_src_files(src_files: dict[str, bytes]) -> dict[str, bytes]:
    """Drop anything from the app's own source that could hijack the
    toolchain: user vite configs (any extension), .npmrc, and index.html
    (Bifrost owns the HTML shell for the same reason it owns the Vite
    config — asset injection paths must stay controlled)."""
    dropped = _VITE_CONFIG_NAMES | {".npmrc", "index.html"}
    return {rel: content for rel, content in src_files.items() if rel not in dropped}


def _build_package_json(src_files: dict[str, bytes], dependencies: dict[str, str]) -> bytes:
    """Merge catalog-validated deps + the vendored SDK tarball ref into
    package.json, always stripping any `scripts` block — Bifrost's builder
    controls invocation of npm install / vite build; there's no reason to
    run an app-supplied npm script during that step."""
    deps = {**dependencies, "bifrost": f"file:./{_SDK_TARBALL}"}

    existing_raw = src_files.get("package.json")
    if existing_raw is not None:
        pkg = json.loads(existing_raw)
    else:
        pkg = {"name": "bifrost-app", "private": True}

    pkg.setdefault("dependencies", {})
    pkg["dependencies"] = {**pkg["dependencies"], **deps}
    pkg.pop("scripts", None)

    return json.dumps(pkg, indent=2).encode()


def materialize_build_input(
    dest_dir: Path, app_id: UUID | str, src_files: dict[str, bytes], dependencies: dict[str, str]
) -> None:
    """Lay out ``src/``, package.json (catalog-pinned deps + vendored SDK
    tarball ref), the Bifrost-owned ``vite.config.mjs`` and ``index.html``,
    and the SDK tarball — refactored out of
    ``SolutionAppBuilder._materialize``.

    Strips/overrides any user-supplied ``vite.config.*``, ``.npmrc``, and
    ``index.html`` from ``src_files``, and drops any user-supplied
    ``package.json`` ``scripts`` block.
    """
    from shared.version import get_version
    from src.services.sdk_package import build_sdk_tarball

    sanitized = _sanitize_src_files(src_files)

    for rel, content in sanitized.items():
        dest = dest_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    (dest_dir / _SDK_TARBALL).write_bytes(build_sdk_tarball(get_version()))
    (dest_dir / "vite.config.mjs").write_text(_BIFROST_VITE_CONFIG, encoding="utf-8")
    (dest_dir / "index.html").write_text(_BIFROST_INDEX_HTML, encoding="utf-8")
    (dest_dir / "package.json").write_bytes(_build_package_json(src_files, dependencies))


def make_input_zip(
    dest_zip: Path, app_id: UUID | str, src_files: dict[str, bytes], dependencies: dict[str, str]
) -> str:
    """Materialize into a tempdir, zip it deterministically (sorted names,
    zeroed timestamps), and return the sha256 of the zip bytes.

    Same-input-same-sha: two separate calls with identical arguments produce
    byte-identical archives, which is what lets a caller treat the digest as
    an idempotent "this input already exists" check.
    """
    with tempfile.TemporaryDirectory(prefix=f"bifrost-buildinput-{app_id}-") as tmp:
        workdir = Path(tmp)
        materialize_build_input(workdir, app_id, src_files, dependencies)

        members = sorted(
            (path, path.relative_to(workdir).as_posix())
            for path in workdir.rglob("*")
            if path.is_file() and not path.is_symlink()
        )

        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, member in members:
                info = zipfile.ZipInfo(member, date_time=_ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())

    digest = hashlib.sha256()
    with dest_zip.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
