"""Pick which Solution app `bifrost solution start` serves."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class AppSelectionError(Exception):
    """No usable app (or an ambiguous/unknown choice)."""


@dataclass(frozen=True)
class ChosenApp:
    app_id: str
    slug: str
    app_dir: Path


def select_app(workspace: Path, *, slug: str | None) -> ChosenApp:
    manifest = workspace / ".bifrost" / "apps.yaml"
    data = yaml.safe_load(manifest.read_text()) if manifest.is_file() else None
    apps = (data or {}).get("apps", {}) or {}
    v2 = [
        b for b in apps.values()
        if isinstance(b, dict) and b.get("app_model") == "standalone_v2"
    ]

    if slug is not None:
        for b in v2:
            if b.get("slug") == slug:
                return _to_chosen(workspace, b)
        available = ", ".join(sorted(b.get("slug", "?") for b in v2)) or "(none)"
        raise AppSelectionError(f"No standalone_v2 app '{slug}'. Available: {available}")

    if not v2:
        raise AppSelectionError(
            "No standalone_v2 app in this workspace. "
            "Create one with `bifrost solution scaffold-app <slug>`."
        )
    if len(v2) > 1:
        listing = ", ".join(sorted(b.get("slug", "?") for b in v2))
        raise AppSelectionError(
            f"Multiple apps found ({listing}). "
            f"Name one: `bifrost solution start <slug>`."
        )
    return _to_chosen(workspace, v2[0])


def _to_chosen(workspace: Path, body: dict) -> ChosenApp:
    return ChosenApp(
        app_id=str(body["id"]),
        slug=str(body.get("slug") or body["id"]),
        app_dir=workspace / str(body["path"]),
    )
