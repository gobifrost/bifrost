"""Standard ``.env`` binding for an independently managed App project."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

APP_ENV_KEYS = {"BIFROST_API_URL", "BIFROST_APP_ID"}


@dataclass(frozen=True)
class AppBinding:
    api_url: str
    app_id: str


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ")
    key, value = stripped.split("=", 1)
    return key.strip(), value.strip().strip('"').strip("'")


def read_app_binding(root: Path) -> AppBinding | None:
    path = root / ".env"
    if not path.is_file():
        return None
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            values[parsed[0]] = parsed[1]
    api_url = values.get("BIFROST_API_URL")
    app_id = values.get("BIFROST_APP_ID")
    return AppBinding(api_url=api_url, app_id=app_id) if api_url and app_id else None


def write_app_binding(root: Path, binding: AppBinding) -> None:
    path = root / ".env"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept = []
    for line in existing:
        parsed = _parse_env_line(line)
        if parsed is None or parsed[0] not in APP_ENV_KEYS:
            kept.append(line)
    additions = [
        f"BIFROST_API_URL={binding.api_url.rstrip('/')}",
        f"BIFROST_APP_ID={binding.app_id}",
    ]
    path.write_text("\n".join([*kept, *additions]).rstrip() + "\n", encoding="utf-8")


def find_app_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "package.json").is_file() and read_app_binding(candidate):
            return candidate
    return None
