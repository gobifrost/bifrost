"""Catalog-validated build input materialization for the private Solution
builder's server-side app compile step.

An AI-generated app's ``dependencies`` dict comes from whatever the agent
turn declared — it is untrusted input to the toolchain. Two gates apply
before it ever reaches ``npm install``:

1. :func:`validate_dependencies` — every package must match a catalog pin.
   A scaffold caret range is accepted only when it contains the curated pin;
   materialization rewrites it to the exact version before npm sees it.
2. :func:`materialize_build_input` strips/overrides anything in the app's
   own source that could hijack the build (user-supplied Vite, PostCSS, or
   Tailwind config, executable Tailwind CSS directives, ``.npmrc``,
   ``index.html``, or npm lifecycle scripts) — Bifrost, not the generated app,
   controls how the toolchain is invoked.

This module is the extraction target for what used to live inline in
``SolutionAppBuilder._materialize`` (api/src/services/solutions/app_build.py);
that method is now a thin call into :func:`materialize_build_input`.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path, PurePosixPath
from uuid import UUID

from src.services.builder.scaffold import zip_workspace

_CATALOG_PATH = Path(__file__).resolve().parents[3] / "shared" / "builder_package_catalog.json"

# The app resolves `import ... from "bifrost"` against this local tarball —
# same mechanism/name SolutionAppBuilder already vendors under.
_SDK_TARBALL = "bifrost-sdk.tgz"
_BUILD_META = "build-meta.json"

# Build-toolchain packages the Bifrost-owned vite.config.mjs imports. They are
# ALWAYS written into the materialized package.json (as devDependencies, at
# the catalog's exact pins) so the config's imports resolve even when the app
# declares no toolchain of its own — and so an app-declared range can never
# drift the toolchain off the versions TOOLCHAIN_VERSION advertises.
_TOOLCHAIN_PACKAGES = (
    "@tailwindcss/vite",
    "@vitejs/plugin-react",
    "tailwindcss",
    "typescript",
    "vite",
)

# The vendored ``bifrost`` SDK declares these as peer dependencies. npm would
# otherwise choose a newer version from cached registry metadata even though
# the runner intentionally preloads only the curated catalog. Force every peer
# to its exact catalog pin so an app with no explicit dependencies is still a
# complete, deterministic offline build input.
_SDK_PEER_PACKAGES = ("lucide-react", "react", "react-dom")

# Root-level user vite configs are stripped: vite auto-loads them, and a config
# file executes arbitrary node code at build time.
_VITE_CONFIG_NAMES = {
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.cjs",
    "vite.config.ts",
    "vite.config.mts",
    "vite.config.cts",
}

# Vite's fixed ``--config`` does not constrain the independent PostCSS and
# Tailwind config discovery paths. Those formats can load JavaScript from the
# submitted workspace, so the fixed-contract builder removes every supported
# spelling at any depth. Package-level PostCSS settings are already discarded
# when ``package.json`` is reconstructed below.
_CSS_CONFIG_NAMES = {
    ".postcssrc",
    ".postcssrc.json",
    ".postcssrc.yaml",
    ".postcssrc.yml",
    ".postcssrc.js",
    ".postcssrc.mjs",
    ".postcssrc.cjs",
    ".postcssrc.ts",
    ".postcssrc.mts",
    ".postcssrc.cts",
    "postcss.config.js",
    "postcss.config.mjs",
    "postcss.config.cjs",
    "postcss.config.ts",
    "postcss.config.mts",
    "postcss.config.cts",
    "tailwind.config.js",
    "tailwind.config.mjs",
    "tailwind.config.cjs",
    "tailwind.config.ts",
    "tailwind.config.mts",
    "tailwind.config.cts",
}


def dist_base(app_id: UUID | str, solution_id: UUID | str | None = None) -> str:
    """The public URL prefix a built app's assets are served from. Single
    source of truth: baked into the Bifrost vite config here AND passed as
    ``vite build --base`` by the runner — the two must agree.

    Builder-owned private apps use their isolated app-host path. The legacy
    API dist path remains only for existing non-builder source builds while
    callers migrate to the dedicated build plane.
    """
    if solution_id is not None:
        return f"/{solution_id}/apps/{app_id}/"
    return f"/api/applications/{app_id}/dist/"


def _bifrost_vite_config(
    app_id: UUID | str, solution_id: UUID | str | None = None
) -> str:
    # Mirrors the build-relevant half of the `bifrost solution scaffold-app`
    # vite.config.ts (react + tailwind v4 plugins, `@` → src alias for shadcn
    # imports) minus its dev-server token plumbing, which has no meaning in a
    # server-side `vite build`. `base` is baked in so the input zip is fully
    # self-describing for an out-of-process build container; the in-process
    # builder passes the SAME value on the CLI (--base), which takes
    # precedence and keeps the two paths identical.
    return f"""\
import {{ join }} from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import {{ defineConfig }} from "vite";

// Bifrost-owned build config. User vite configs are stripped from the build
// input (a config file executes arbitrary code at build time); this is the
// entire toolchain surface an app gets.
export default defineConfig({{
  base: "{dist_base(app_id, solution_id)}",
  plugins: [react(), tailwindcss()],
  resolve: {{ alias: {{ "@": join(process.cwd(), "src") }} }},
}});
"""


# Mirrors the `bifrost solution scaffold-app` index.html. The
# bifrost-app-runtime meta tag is LOAD-BEARING: the app host detects the
# mount-v1 runtime contract from the built HTML (src/routers/app_code_files.py)
# and the client mounts the app through mount() only when it's present.
_BIFROST_INDEX_HTML = """\
<!doctype html>
<html lang="en" class="h-full">
  <head>
    <meta charset="UTF-8" />
    <meta name="bifrost-app-runtime" content="mount-v1" />
    <title>Bifrost App</title>
  </head>
  <body class="h-full">
    <div id="root" class="h-full"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""


class UnsupportedDependency(Exception):
    """Raised when a caller-declared dependency isn't an exact catalog match.

    ``offenders`` maps each rejected package name to the version the caller
    requested — for both a package absent from the catalog entirely and a
    catalog package pinned to the wrong version.
    """

    def __init__(self, offenders: dict[str, str]):
        self.offenders = dict(offenders)
        detail = ", ".join(f"{name}@{version}" for name, version in sorted(offenders.items()))
        super().__init__(f"Unsupported build dependencies: {detail}")


def load_package_catalog() -> dict[str, str]:
    """Parse ``api/shared/builder_package_catalog.json`` into {name: exact_version}."""
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def _accepts_catalog_pin(requested: str, expected: str) -> bool:
    """Whether an exact version or npm caret lower-bound contains ``expected``.

    This deliberately implements only the numeric ``^X.Y.Z`` form emitted by
    Bifrost scaffolds. It is enough to keep previously generated
    ``typescript@^5.4.0`` apps buildable after the curated pin moved to the
    first real 5.4 stable patch, without admitting arbitrary npm range syntax.
    """
    if requested == expected:
        return True
    if not requested.startswith("^"):
        return False
    try:
        lower = tuple(int(part) for part in requested[1:].split("."))
        pin = tuple(int(part) for part in expected.split("."))
    except ValueError:
        return False
    if len(lower) != 3 or len(pin) != 3 or pin < lower:
        return False
    if lower[0] > 0:
        return pin[0] == lower[0]
    if lower[1] > 0:
        return pin[:2] == lower[:2]
    return pin == lower


def validate_dependencies(dependencies: dict[str, str]) -> None:
    """Raise :class:`UnsupportedDependency` for any package not in the catalog
    or pinned to a version other than the catalog's exact pin.

    Existing Bifrost scaffolds declare numeric caret ranges. A range is accepted
    only when it contains the catalog pin, then rewritten to that exact pin in
    the materialized package. No other range syntax is honored. The
    ``bifrost`` SDK dep is exempt: materialization force-rewrites it to the
    vendored local tarball regardless of what was declared.
    """
    catalog = load_package_catalog()
    offenders = {
        name: version
        for name, version in dependencies.items()
        if name != "bifrost"
        and (
            (expected := catalog.get(name)) is None
            or not _accepts_catalog_pin(version, expected)
        )
    }
    if offenders:
        raise UnsupportedDependency(offenders)


def _sanitize_src_files(src_files: dict[str, bytes]) -> dict[str, bytes]:
    """Drop anything from the app's own source that could hijack the
    toolchain: root-level vite configs and index.html (both replaced by the
    Bifrost-owned versions), plus package-manager and CSS-tool configs at any
    depth. Reject Tailwind directives that load workspace JavaScript directly;
    unlike ordinary CSS directives, ``@config`` and ``@plugin`` execute code
    during the build."""
    dropped_root = _VITE_CONFIG_NAMES | {
        "index.html",
        "package-lock.json",
        "npm-shrinkwrap.json",
    }
    sanitized: dict[str, bytes] = {}
    for rel, content in src_files.items():
        name = PurePosixPath(rel).name
        parts = PurePosixPath(rel).parts
        if (
            rel in dropped_root
            or name == ".npmrc"
            or name in _CSS_CONFIG_NAMES
            or "node_modules" in parts
            or (parts and parts[0] == "dist")
        ):
            continue
        if PurePosixPath(rel).suffix.lower() in {".css", ".pcss", ".postcss"}:
            css = content.decode("utf-8", errors="ignore").lower()
            if "@config" in css or "@plugin" in css:
                raise ValueError(
                    f"executable Tailwind directive is not allowed in build input: {rel!r}"
                )
        sanitized[rel] = content
    return sanitized


def _source_target(dest_dir: Path, rel_path: str) -> Path:
    """Resolve one archive-relative source path beneath ``dest_dir``."""
    pure = PurePosixPath(rel_path)
    if (
        not pure.parts
        or pure.is_absolute()
        or ".." in pure.parts
        or "\x00" in rel_path
        or "\\" in rel_path
    ):
        raise ValueError(f"unsafe build input path: {rel_path!r}")
    return dest_dir.joinpath(*pure.parts)


def _dependency_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise UnsupportedDependency({"<package.json>": "dependencies must be an object"})
    if any(not isinstance(name, str) or not isinstance(version, str) for name, version in value.items()):
        raise UnsupportedDependency({"<package.json>": "dependency names and versions must be strings"})
    return value


def _pinned_dependencies(dependencies: dict[str, str]) -> dict[str, str]:
    catalog = load_package_catalog()
    validate_dependencies(dependencies)
    return {
        name: catalog[name]
        for name in dependencies
        if name != "bifrost"
    }


def _build_package_json(src_files: dict[str, bytes], dependencies: dict[str, str]) -> bytes:
    """Build the fixed-contract package.json.

    Both manifest dependencies and declarations inside the app's source
    ``package.json`` are untrusted, so the catalog gate covers their union.
    Only build-relevant metadata survives; npm scripts, workspaces, registry
    knobs, and other package-manager behavior cannot ride through source.
    """
    catalog = load_package_catalog()

    existing_raw = src_files.get("package.json")
    if existing_raw is not None:
        loaded = json.loads(existing_raw)
        if not isinstance(loaded, dict):
            raise UnsupportedDependency({"<package.json>": "root must be an object"})
    else:
        loaded = {}

    source_dependencies = _dependency_map(loaded.get("dependencies", {}))
    source_dev_dependencies = _dependency_map(loaded.get("devDependencies", {}))
    merged_dependencies = {**source_dependencies, **dependencies}
    validate_dependencies({**merged_dependencies, **source_dev_dependencies})

    pkg = {
        "name": loaded.get("name") if isinstance(loaded.get("name"), str) else "bifrost-app",
        "private": True,
        "type": "module",
        "dependencies": {
            **{name: catalog[name] for name in _SDK_PEER_PACKAGES},
            **_pinned_dependencies(merged_dependencies),
            "bifrost": f"file:./{_SDK_TARBALL}",
        },
        "devDependencies": {
            **_pinned_dependencies(source_dev_dependencies),
            **{name: catalog[name] for name in _TOOLCHAIN_PACKAGES},
        },
    }
    return json.dumps(pkg, indent=2, sort_keys=True).encode()


def materialize_build_input(
    dest_dir: Path,
    app_id: UUID | str,
    src_files: dict[str, bytes],
    dependencies: dict[str, str],
    *,
    solution_id: UUID | str | None = None,
) -> None:
    """Lay out ``src/``, package.json (catalog-pinned deps + vendored SDK
    tarball ref + pinned toolchain devDependencies), the Bifrost-owned
    ``vite.config.mjs`` and ``index.html``, and the SDK tarball — refactored
    out of ``SolutionAppBuilder._materialize``.

    Strips user-supplied Vite/PostCSS/Tailwind config, ``.npmrc``, and
    ``index.html`` from ``src_files``; rejects executable Tailwind directives;
    and drops any user-supplied ``package.json`` ``scripts`` block.
    """
    from shared.version import get_version
    from src.services.sdk_package import build_sdk_tarball

    for rel, content in _sanitize_src_files(src_files).items():
        dest = _source_target(dest_dir, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    (dest_dir / _SDK_TARBALL).write_bytes(build_sdk_tarball(get_version()))
    (dest_dir / "vite.config.mjs").write_text(
        _bifrost_vite_config(app_id, solution_id), encoding="utf-8"
    )
    (dest_dir / "index.html").write_text(_BIFROST_INDEX_HTML, encoding="utf-8")
    (dest_dir / _BUILD_META).write_text(
        json.dumps(
            {
                "app_id": str(app_id),
                "base": dist_base(app_id, solution_id),
                **({"solution_id": str(solution_id)} if solution_id is not None else {}),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (dest_dir / "package.json").write_bytes(_build_package_json(src_files, dependencies))


def make_input_zip(
    dest_zip: Path,
    app_id: UUID | str,
    src_files: dict[str, bytes],
    dependencies: dict[str, str],
    *,
    solution_id: UUID | str | None = None,
) -> str:
    """Materialize into a tempdir, zip it deterministically (sorted names,
    zeroed timestamps — ``scaffold.zip_workspace``'s convention), and return
    the sha256 of the zip bytes.

    Same-input-same-sha: identical arguments produce byte-identical archives,
    including across processes. The vendored SDK tarball uses a fixed gzip
    timestamp for this reason.
    """
    with tempfile.TemporaryDirectory(prefix=f"bifrost-buildinput-{app_id}-") as tmp:
        workdir = Path(tmp)
        materialize_build_input(
            workdir,
            app_id,
            src_files,
            dependencies,
            solution_id=solution_id,
        )
        return zip_workspace(workdir, dest_zip)
