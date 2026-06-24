"""
Solution export — serialize Solution workspace zips.

``POST /api/solutions/{id}/export`` calls
:func:`build_workspace_zip` on every request so the zip always reflects
current ownership unless the install has a deploy-time source artifact. In that
case shareable export returns the stored artifact, and full export overlays the
encrypted runtime payload onto that artifact.

The zip is the same shape ``preview_zip``/``install_zip`` consume:
``bifrost.solution.yaml`` + ``.bifrost/*.yaml`` manifests + Python source +
app source dirs — so an export is directly re-installable.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from src.services.solutions.deploy import SolutionBundle
    from src.services.solutions.secrets_blob import SolutionContent

# Reverse of the CLI's logo suffix → content-type map (deploy re-validates).
_LOGO_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
}

# Fixed timestamp so a byte-identical bundle exports byte-identically (the
# finalize step retries idempotently; zip member mtimes must not churn).
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Bundle-transport fields that are NOT part of an app's manifest entry — the
# files land in the app's source dir, the logos as real files referenced by
# the ``logo:`` key. ``dist_files``/``bin_dist_files`` (the prebuilt fast-path)
# are build OUTPUT, normally not part of a workspace — but a prebuilt-only app
# has no source, so its dist is re-added to the manifest body below.
_APP_TRANSPORT_FIELDS = (
    "src_files",
    "bin_files",
    "dist_files",
    "bin_dist_files",
    "logo_b64",
    "logo_content_type",
)


def _safe_dir(name: str) -> str:
    """A slug is validated platform-side, but never trust it as a path."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", name) or "app"


def _manifest_yaml(root_key: str, bodies: dict[str, dict[str, Any]]) -> str:
    return yaml.safe_dump({root_key: bodies}, sort_keys=False, allow_unicode=True)


def build_workspace_zip(bundle: "SolutionBundle", *, password: str | None = None) -> bytes:
    """Serialize a (pre-remap) bundle into the installable workspace-zip shape.

    When ``password`` is provided and the bundle carries sensitive values
    (config_values or table_data), they are encrypted into ``.bifrost/secrets.enc``
    using the password.  Shareable exports (no password) never include the blob.
    """
    solution = bundle.solution
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        def put(path: str, data: bytes | str) -> None:
            info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)

        # ── Descriptor ───────────────────────────────────────────────────────
        descriptor: dict[str, Any] = {
            "slug": solution.slug,
            "name": solution.name,
        }
        version = bundle.version or solution.version
        if version:
            descriptor["version"] = version
        # No ``scope`` in the descriptor — install kind is the installer's
        # deploy-time choice (--org/--global), derived server-side from
        # organization_id. The exported descriptor is pure definition.
        descriptor["global_repo_access"] = bool(solution.global_repo_access)
        if bundle.logo_b64 and bundle.logo_content_type in _LOGO_EXTENSIONS:
            logo_name = f"solution-logo{_LOGO_EXTENSIONS[bundle.logo_content_type]}"
            descriptor["logo"] = logo_name
            put(logo_name, base64.b64decode(bundle.logo_b64))
        put("bifrost.solution.yaml", yaml.safe_dump(descriptor, sort_keys=False))

        # ── Python source (workflows + modules, verbatim) ────────────────────
        for rel, content in sorted(bundle.python_files.items()):
            put(rel, content)

        # ── Entity manifests (.bifrost/*.yaml, keyed by manifest id) ────────
        if bundle.workflows:
            put(
                ".bifrost/workflows.yaml",
                _manifest_yaml(
                    "workflows", {str(e["id"]): dict(e) for e in bundle.workflows}
                ),
            )
        if bundle.tables:
            put(
                ".bifrost/tables.yaml",
                _manifest_yaml("tables", {str(e["id"]): dict(e) for e in bundle.tables}),
            )
        if bundle.forms:
            put(
                ".bifrost/forms.yaml",
                _manifest_yaml("forms", {str(e["id"]): dict(e) for e in bundle.forms}),
            )
        if bundle.agents:
            put(
                ".bifrost/agents.yaml",
                _manifest_yaml("agents", {str(e["id"]): dict(e) for e in bundle.agents}),
            )
        if bundle.claims:
            put(
                ".bifrost/claims.yaml",
                _manifest_yaml("claims", {str(e["id"]): dict(e) for e in bundle.claims}),
            )
        if bundle.config_schemas:
            put(
                ".bifrost/configs.yaml",
                _manifest_yaml(
                    "configs", {str(e["key"]): dict(e) for e in bundle.config_schemas}
                ),
            )
        if bundle.file_locations:
            put(
                ".bifrost/files.yaml",
                yaml.safe_dump(
                    {"locations": list(bundle.file_locations)},
                    sort_keys=False,
                    allow_unicode=True,
                ),
            )
        # Connection declarations (integrations.get("X") refs) — keyed by the
        # integration NAME (the natural key; no per-install id). Each entry is a
        # secret-scrubbed {integration_name, template, position} dict, the same
        # shape _upsert_integration_shells / setup_status consume on install.
        if bundle.connection_schemas:
            put(
                ".bifrost/connections.yaml",
                _manifest_yaml(
                    "connections",
                    {
                        str(e["integration_name"]): dict(e)
                        for e in bundle.connection_schemas
                    },
                ),
            )

        # Event/schedule triggers — keyed by EventSource id. Webhook instance
        # secrets are already scrubbed by capture (serialize_event_source omits
        # state/external_id/expires_at); the portable definition + subscriptions
        # travel, the instance re-establishes external state after install.
        if bundle.events:
            put(
                ".bifrost/events.yaml",
                _manifest_yaml("events", {str(e["id"]): dict(e) for e in bundle.events}),
            )

        # ── Long-form README markdown at the repo root (deploy-owned) ────────
        if bundle.readme:
            put("README.md", bundle.readme)

        # ── Apps: manifest entry + source dir + logo file ────────────────────
        if bundle.apps:
            app_bodies: dict[str, dict[str, Any]] = {}
            for app in bundle.apps:
                body = {k: v for k, v in app.items() if k not in _APP_TRANSPORT_FIELDS}
                app_dir = f"apps/{_safe_dir(str(app.get('slug') or app['id']))}"
                body["path"] = app_dir
                has_src = bool(app.get("src_files") or app.get("bin_files"))
                for rel, text in sorted((app.get("src_files") or {}).items()):
                    put(f"{app_dir}/{rel}", text)
                for rel, b64 in sorted((app.get("bin_files") or {}).items()):
                    put(f"{app_dir}/{rel}", base64.b64decode(b64))
                logo_b64 = app.get("logo_b64")
                logo_ct = app.get("logo_content_type")
                if logo_b64 and logo_ct in _LOGO_EXTENSIONS:
                    logo_rel = f"app-logo{_LOGO_EXTENSIONS[logo_ct]}"
                    body["logo"] = logo_rel
                    put(f"{app_dir}/{logo_rel}", base64.b64decode(logo_b64))
                # Prebuilt-only apps (no src or bin files) were deployed via the
                # dist_files fast-path. The standard export strips dist_files (build
                # output) from the manifest, but when there is no source the dist IS
                # the only representation — carry it in the manifest body so the
                # deployer can use the prebuilt fast-path on re-install without
                # triggering a Vite build on an empty workdir.
                if not has_src:
                    dist = app.get("dist_files")
                    if dist:
                        body["dist_files"] = dist
                    bin_dist = app.get("bin_dist_files")
                    if bin_dist:
                        body["bin_dist_files"] = bin_dist
                app_bodies[str(app["id"])] = body
            put(".bifrost/apps.yaml", _manifest_yaml("apps", app_bodies))

        # ── Encrypted secrets blob (full-mode export only) ───────────────────
        # File sidecars join config_values and table_data in the encrypted tier.
        # Content bytes are base64-encoded for JSON serialization; the index
        # (location/path/sha256/size) travels alongside for enumeration.
        # All three are CONFIDENTIAL — never written as plaintext zip members.
        file_sidecar_entries: list[dict[str, Any]] = []
        if password and bundle.solution_files:
            for sf in bundle.solution_files:
                if sf.content_bytes is None:
                    continue
                file_sidecar_entries.append(
                    {
                        "location": sf.location,
                        "path": sf.path,
                        "sha256": sf.sha256,
                        "size": sf.size,
                        "content_b64": base64.b64encode(sf.content_bytes).decode("ascii"),
                    }
                )
        if password and (bundle.config_values or bundle.table_data or file_sidecar_entries):
            from src.services.solutions.secrets_blob import (
                SolutionContent,
                encode_secrets_blob,
            )

            put(
                ".bifrost/secrets.enc",
                encode_secrets_blob(
                    SolutionContent(
                        config_values=bundle.config_values,
                        table_data=bundle.table_data,
                        solution_files=file_sidecar_entries,
                    ),
                    password=password,
                ),
            )

    return buf.getvalue()


def add_encrypted_content_to_workspace_zip(
    source_zip: bytes,
    content: "SolutionContent",
    *,
    password: str,
) -> bytes:
    """Return ``source_zip`` plus a fresh encrypted runtime-content blob.

    The stored source artifact is immutable deploy input. A full backup export
    should not rebuild that source from DB state; it should copy the artifact and
    overlay the runtime payload as ``.bifrost/secrets.enc`` in the response zip.
    If the source already contains that member, replace it so repeated full
    exports never produce duplicate zip entries.
    """
    from src.services.solutions.secrets_blob import encode_secrets_blob

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source_zip), "r") as src, zipfile.ZipFile(
        buf, "w", zipfile.ZIP_DEFLATED
    ) as dst:

        def put(path: str, data: bytes | str) -> None:
            info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(info, data)

        for name in src.namelist():
            if name == ".bifrost/secrets.enc":
                continue
            put(name, src.read(name))

        put(".bifrost/secrets.enc", encode_secrets_blob(content, password=password))

    return buf.getvalue()
