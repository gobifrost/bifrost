from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, cast


SOLUTION_ENV_KEYS = {
    "BIFROST_SOLUTION_ID",
    "BIFROST_SOLUTION_SLUG",
    "BIFROST_SOLUTION_ORG_ID",
    "BIFROST_SOLUTION_SCOPE",
}


@dataclass(frozen=True)
class SolutionBinding:
    solution_id: str
    slug: str
    organization_id: str | None
    scope: Literal["org", "global"]


class SolutionBindingError(ValueError):
    pass


def _env_path(workspace: Path) -> Path:
    return workspace / ".env"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :]
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    return key, value


def read_solution_binding(workspace: Path) -> SolutionBinding | None:
    env = _env_path(workspace)
    if not env.is_file():
        return None
    values: dict[str, str] = {}
    for line in env.read_text().splitlines():
        parsed = _parse_env_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    solution_id = values.get("BIFROST_SOLUTION_ID")
    slug = values.get("BIFROST_SOLUTION_SLUG")
    if not solution_id or not slug:
        return None
    scope = values.get("BIFROST_SOLUTION_SCOPE") or "org"
    if scope not in {"org", "global"}:
        return None
    org_id = values.get("BIFROST_SOLUTION_ORG_ID") or None
    if scope == "global":
        org_id = None
    elif org_id is None:
        return None
    scope_value = cast(Literal["org", "global"], scope)
    return SolutionBinding(
        solution_id=solution_id,
        slug=slug,
        organization_id=org_id,
        scope=scope_value,
    )


def write_solution_binding(workspace: Path, binding: SolutionBinding) -> None:
    env = _env_path(workspace)
    existing = env.read_text().splitlines() if env.is_file() else []
    kept = []
    for line in existing:
        parsed = _parse_env_line(line)
        if parsed is None or parsed[0] not in SOLUTION_ENV_KEYS:
            kept.append(line)
    additions = [
        f"BIFROST_SOLUTION_ID={binding.solution_id}",
        f"BIFROST_SOLUTION_SLUG={binding.slug}",
        f"BIFROST_SOLUTION_ORG_ID={binding.organization_id or ''}",
        f"BIFROST_SOLUTION_SCOPE={binding.scope}",
    ]
    env.write_text("\n".join([*kept, *additions]).rstrip() + "\n")


def binding_from_install(
    install: Mapping[str, Any],
    *,
    descriptor_slug: str,
) -> SolutionBinding:
    """Build a binding from an install, requiring its slug to match the descriptor."""
    solution_id = str(install.get("id") or "")
    if not solution_id:
        raise SolutionBindingError("Install is missing id")
    slug = str(install.get("slug") or "")
    if not slug:
        raise SolutionBindingError("Install is missing slug")
    if slug != descriptor_slug:
        raise SolutionBindingError(
            f"Install slug {slug!r} does not match descriptor slug {descriptor_slug!r}"
        )
    org_id = install.get("organization_id")
    return SolutionBinding(
        solution_id=solution_id,
        slug=slug,
        organization_id=str(org_id) if org_id else None,
        scope="org" if org_id else "global",
    )


def resolve_install_ref(
    installs: Sequence[Mapping[str, Any]],
    ref: str,
    *,
    descriptor_slug: str,
) -> SolutionBinding:
    """Resolve an install by id first, then unique slug, validating descriptor slug."""
    matches = [s for s in installs if s.get("id") == ref]
    if not matches:
        matches = [s for s in installs if s.get("slug") == ref]
    if not matches:
        raise SolutionBindingError(f"No solution install found for {ref!r}")
    if len(matches) > 1:
        ids = ", ".join(str(m.get("id")) for m in matches)
        raise SolutionBindingError(
            f"Slug {ref!r} matches multiple installs ({ids}); pass an install id"
        )
    return binding_from_install(matches[0], descriptor_slug=descriptor_slug)
