"""Initial workspace scaffold and deterministic zipping for builder revisions.

A builder project starts from the smallest thing the Solution workspace parser
accepts: a ``bifrost.solution.yaml`` carrying ``slug`` + ``name``. Everything
else in a workspace (``.bifrost/*.yaml`` manifests, apps, workflow modules) is
what the agent adds over subsequent turns, so scaffolding more would be
inventing content the user did not ask for.

Revision zips are content-addressed: the sha256 of the archive bytes is the
revision's identity and the signal for "this turn changed nothing". That only
works if identical trees produce identical bytes, so :func:`zip_workspace`
writes members in sorted order with a fixed timestamp — the same
``_ZIP_EPOCH`` convention the Solution exporter uses.

Directory entries are deliberately never written: ``safe_extract_zip`` refuses
directory members, so a directory only survives a round trip by way of a file
inside it (hence ``workflows/.gitkeep``).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml

from bifrost.solution_descriptor import DESCRIPTOR_FILENAME, load_descriptor

# Same fixed DOS timestamp the Solution exporter stamps on members. Zip's date
# fields start at 1980, so this is the earliest value that round-trips.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

_README_TEMPLATE = """# {name}

Built with the Bifrost Solution builder.
"""

BUILDER_SKILL_BUNDLE_PATH = "skills/bifrost-build"
BUILDER_AGENT_MAX_ITERATIONS = 80
# Coding harness context is cumulative across model/tool iterations. A normal
# full-app turn used about 466k input tokens in its first 14 calls even though
# it generated fewer than 3k output tokens. Keep the turn finite while leaving
# enough headroom for roughly 60 compacted coding steps. The token ceiling is
# the primary cost guardrail; the higher call ceiling still stops tool loops.
BUILDER_AGENT_MAX_TOKEN_BUDGET = 2_000_000
BUILDER_AGENT_SYSTEM_TOOLS = [
    "list_files",
    "read_file",
    "search_text",
    "write_file",
    "apply_patch",
    "delete_file",
    "make_directory",
    "validate_solution",
    "test_solution_build",
]
_BUILDER_AGENT_ID_KEY = "bifrost-private-solution-builder-agent"


def _builder_skill_source() -> Path:
    configured = os.getenv("BIFROST_BUILDER_SKILL_PATH")
    candidates = [
        Path(configured) if configured else None,
        Path("/app/.claude/skills/bifrost-build"),
        Path(__file__).resolve().parents[4] / ".claude/skills/bifrost-build",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "SKILL.md").is_file():
            return candidate
    raise FileNotFoundError("canonical bifrost-build skill bundle is unavailable")


def builder_agent_id(solution_id: UUID) -> UUID:
    return uuid5(solution_id, _BUILDER_AGENT_ID_KEY)


def build_initial_workspace(
    dir: Path,
    *,
    slug: str,
    name: str,
    solution_id: UUID | None = None,
) -> None:
    """Write a minimal valid Solution workspace into ``dir``.

    ``dir`` is created if absent. The result parses as a Solution workspace and
    passes :func:`validate_workspace`.
    """
    dir.mkdir(parents=True, exist_ok=True)

    descriptor = yaml.safe_dump({"slug": slug, "name": name}, sort_keys=False, allow_unicode=True)
    (dir / DESCRIPTOR_FILENAME).write_text(descriptor, encoding="utf-8")

    skill_source = _builder_skill_source()
    agent_id = builder_agent_id(
        solution_id or uuid5(NAMESPACE_URL, f"bifrost-builder:{slug}")
    )

    workflows = dir / "workflows"
    workflows.mkdir(exist_ok=True)
    (workflows / ".gitkeep").write_text("", encoding="utf-8")

    (dir / "README.md").write_text(_README_TEMPLATE.format(name=name), encoding="utf-8")
    shutil.copytree(
        skill_source,
        dir / BUILDER_SKILL_BUNDLE_PATH,
        symlinks=False,
    )
    manifest_dir = dir / ".bifrost"
    manifest_dir.mkdir(exist_ok=True)
    agents = {
        "agents": {
            str(agent_id): {
                "id": str(agent_id),
                "name": f"{name} Builder",
                "description": "Private Solution authoring agent",
                "system_prompt": (skill_source / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
                "bundle_path": BUILDER_SKILL_BUNDLE_PATH,
                "channels": ["chat"],
                "system_tools": BUILDER_AGENT_SYSTEM_TOOLS,
                "max_iterations": BUILDER_AGENT_MAX_ITERATIONS,
                "max_token_budget": BUILDER_AGENT_MAX_TOKEN_BUDGET,
                "access_level": "role_based",
            }
        }
    }
    (manifest_dir / "agents.yaml").write_text(
        yaml.safe_dump(agents, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def validate_workspace(dir: Path) -> list[str]:
    """Return the reasons ``dir`` is not a usable Solution workspace.

    An empty list means the workspace is valid. Validation is exactly what the
    install path does — parse the descriptor through the shared loader — so a
    revision that validates here is one ``zip_install`` can also read.
    """
    try:
        descriptor = load_descriptor(dir)
    except FileNotFoundError:
        return [f"missing {DESCRIPTOR_FILENAME} at the workspace root"]
    except yaml.YAMLError as exc:
        return [f"{DESCRIPTOR_FILENAME} is not valid YAML: {exc}"]
    except ValueError as exc:
        # pydantic's ValidationError is a ValueError; its message already names
        # the offending fields.
        return [f"{DESCRIPTOR_FILENAME} is invalid: {exc}"]

    errors: list[str] = []
    if not descriptor.slug.strip():
        errors.append(f"{DESCRIPTOR_FILENAME} has an empty slug")
    if not descriptor.name.strip():
        errors.append(f"{DESCRIPTOR_FILENAME} has an empty name")

    # Use the same collector that powers Solution preview and deploy so the
    # Builder's validation result cannot disagree with the release path. In
    # particular, ManifestApp deliberately defaults a missing ``app_model`` to
    # legacy inline_v1; Solution deploy rejects that model because it has no
    # source-build/runtime path.
    try:
        from src.services.solutions.zip_install import _parse_workspace

        preview = _parse_workspace(dir)
    except Exception as exc:  # noqa: BLE001 - return parser failures to the agent
        errors.append(f"Solution manifests are invalid: {exc}")
        return errors
    for app in preview.apps:
        from src.services.builder.build_input import (
            UnsupportedDependency,
            validate_app_dependencies,
        )

        app_model = str(app.get("app_model") or "inline_v1")
        slug = str(app.get("slug") or app.get("name") or "unnamed")
        if app_model != "standalone_v2":
            errors.append(
                f"app '{slug}' must set app_model: standalone_v2 in "
                f".bifrost/apps.yaml; found {app_model!r}"
            )
        src_files = {
            path: content.encode("utf-8") if isinstance(content, str) else content
            for path, content in (app.get("src_files") or {}).items()
        }
        try:
            validate_app_dependencies(src_files, app.get("dependencies") or {})
        except (UnsupportedDependency, ValueError) as exc:
            errors.append(f"app '{slug}' build input is invalid: {exc}")
    return errors


def zip_workspace(dir: Path, zip_path: Path) -> str:
    """Zip ``dir`` into ``zip_path`` deterministically; return the sha256 hex.

    Two workspaces with identical file contents produce byte-identical
    archives, so the digest is a reliable "did this turn change anything"
    comparison. Symlinks are skipped: the workspace tools refuse to create
    them, and following one would copy content from outside the root.
    """
    members = sorted(
        (path, path.relative_to(dir).as_posix())
        for path in dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    )

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, member in members:
            info = zipfile.ZipInfo(member, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            # Regular file, 0644 — never carry the host's umask into the zip.
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())

    digest = hashlib.sha256()
    with zip_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
