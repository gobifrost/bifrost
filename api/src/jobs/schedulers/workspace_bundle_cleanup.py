"""Retention reaper for staged workspace-bundle imports."""

from src.services.solutions.workspace_bundle_storage import cleanup_expired_workspace_bundle_previews


async def cleanup_workspace_bundle_previews() -> int:
    return await cleanup_expired_workspace_bundle_previews()
