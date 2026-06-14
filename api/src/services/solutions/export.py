"""
Solution export — rebuild the workspace zip live from owned entities.

``GET /api/solutions/{id}/export`` calls
:func:`build_workspace_zip` on every request so the zip always reflects
current ownership. No zip is cached to S3; the bundle is serialized on demand.

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
# the ``logo:`` key. ``dist_files`` (the prebuilt fast-path) is build OUTPUT,
# never part of a workspace.
_APP_TRANSPORT_FIELDS = ("src_files", "bin_files", "dist_files", "logo_b64", "logo_content_type")


def _safe_dir(name: str) -> str:
    """A slug is validated platform-side, but never trust it as a path."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", name) or "app"


def _manifest_yaml(root_key: str, bodies: dict[str, dict[str, Any]]) -> str:
    return yaml.safe_dump({root_key: bodies}, sort_keys=False, allow_unicode=True)


def build_workspace_zip(bundle: "SolutionBundle") -> bytes:
    """Serialize a (pre-remap) bundle into the installable workspace-zip shape."""
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
        descriptor["scope"] = "global" if solution.organization_id is None else "org"
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

        # ── Apps: manifest entry + source dir + logo file ────────────────────
        if bundle.apps:
            app_bodies: dict[str, dict[str, Any]] = {}
            for app in bundle.apps:
                body = {k: v for k, v in app.items() if k not in _APP_TRANSPORT_FIELDS}
                app_dir = f"apps/{_safe_dir(str(app.get('slug') or app['id']))}"
                body["path"] = app_dir
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
                app_bodies[str(app["id"])] = body
            put(".bifrost/apps.yaml", _manifest_yaml("apps", app_bodies))

    return buf.getvalue()
