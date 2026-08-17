"""Compile Builder workspace apps through the canonical durable build plane."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from src.services.builder.build_requests import (
    BuildFailed,
    await_build_jobs,
    request_app_build,
)
from src.services.builder.scaffold import validate_workspace
from src.services.solutions.deploy import solution_entity_id
from src.services.solutions.zip_install import _parse_workspace

MAX_MODEL_BUILD_LOG_CHARS = 20_000


class SolutionBuildCheckError(RuntimeError):
    """The current workspace cannot be submitted to the production compiler."""


@dataclass(frozen=True)
class SolutionBuildCheck:
    """Structured result returned to native and MCP Builder harnesses."""

    app_count: int
    build_job_ids: tuple[UUID, ...]
    prebuilt_app_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": True,
            "app_count": self.app_count,
            "compiled_app_count": len(self.build_job_ids),
            "prebuilt_app_count": self.prebuilt_app_count,
            "build_job_ids": [str(job_id) for job_id in self.build_job_ids],
        }


def bounded_build_log_excerpt(exc: BuildFailed) -> str:
    """Keep the useful tail of one compiler log within the tool-result budget."""

    excerpt = (exc.log_excerpt or "").strip()
    if len(excerpt) > MAX_MODEL_BUILD_LOG_CHARS:
        excerpt = "[earlier build output omitted]\n" + excerpt[-MAX_MODEL_BUILD_LOG_CHARS:]
    return excerpt


def model_visible_build_failure(exc: BuildFailed) -> str:
    """Return a bounded compiler failure that gives the model enough to repair."""

    summary = f"Application build {exc.job_id} {exc.status}: {exc}"
    excerpt = bounded_build_log_excerpt(exc)
    if not excerpt:
        return summary
    return f"{summary}\n\nProduction build output:\n{excerpt}"


def _source_bytes(app: dict[str, object]) -> dict[str, bytes]:
    source: dict[str, bytes] = {}
    raw_text = app.get("src_files") or {}
    if not isinstance(raw_text, dict):
        raise SolutionBuildCheckError("app src_files must be an object")
    for rel_path, content in raw_text.items():
        if not isinstance(rel_path, str) or not isinstance(content, (str, bytes)):
            raise SolutionBuildCheckError("app source paths and contents are invalid")
        source[rel_path] = content.encode("utf-8") if isinstance(content, str) else content

    raw_binary = app.get("bin_files") or {}
    if not isinstance(raw_binary, dict):
        raise SolutionBuildCheckError("app bin_files must be an object")
    for rel_path, encoded in raw_binary.items():
        if not isinstance(rel_path, str) or not isinstance(encoded, str):
            raise SolutionBuildCheckError("app binary paths and contents are invalid")
        try:
            source[rel_path] = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise SolutionBuildCheckError(
                f"app binary asset {rel_path!r} is not valid base64"
            ) from exc
    return source


async def test_solution_workspace_build(
    workspace: Path,
    *,
    solution_id: UUID,
    requested_by: UUID,
) -> SolutionBuildCheck:
    """Build every source app exactly as the private deploy path will.

    Successful staged artifacts are intentionally retained. The subsequent
    preview deploy requests the same app id, source, and toolchain, so
    ``request_app_build`` reuses the verified result instead of recompiling it.
    """

    validation_errors = validate_workspace(workspace)
    if validation_errors:
        raise SolutionBuildCheckError("; ".join(validation_errors))

    preview = _parse_workspace(workspace)
    requested_jobs = []
    prebuilt_count = 0
    for app in preview.apps:
        if app.get("dist_files") or app.get("bin_dist_files"):
            prebuilt_count += 1
            continue
        try:
            manifest_app_id = UUID(str(app["id"]))
        except (KeyError, ValueError) as exc:
            raise SolutionBuildCheckError("app manifest id must be a UUID") from exc
        dependencies = app.get("dependencies") or {}
        if not isinstance(dependencies, dict) or any(
            not isinstance(name, str) or not isinstance(version, str)
            for name, version in dependencies.items()
        ):
            raise SolutionBuildCheckError("app dependencies must map names to versions")
        requested_jobs.append(
            await request_app_build(
                solution_id=solution_id,
                app_id=solution_entity_id(solution_id, manifest_app_id),
                requested_by=requested_by,
                src_files=_source_bytes(app),
                dependencies=dependencies,
            )
        )

    completed = await await_build_jobs(requested_jobs)
    return SolutionBuildCheck(
        app_count=len(preview.apps),
        build_job_ids=tuple(job.id for job in completed),
        prebuilt_app_count=prebuilt_count,
    )


__all__ = [
    "MAX_MODEL_BUILD_LOG_CHARS",
    "SolutionBuildCheck",
    "SolutionBuildCheckError",
    "bounded_build_log_excerpt",
    "model_visible_build_failure",
    "test_solution_workspace_build",
]
