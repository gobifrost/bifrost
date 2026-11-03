"""Cleanup helpers for retired per-entity manifest companion files."""

from pathlib import Path, PurePosixPath
from uuid import UUID


LEGACY_ENTITY_SUFFIXES = {
    "forms": ".form.yaml",
    "agents": ".agent.yaml",
}


def is_legacy_entity_file(path: str) -> bool:
    """Return whether path is a historical UUID-named form or agent file."""
    parts = PurePosixPath(path).parts
    if len(parts) != 2:
        return False
    directory, filename = parts
    suffix = LEGACY_ENTITY_SUFFIXES.get(directory)
    if suffix is None or not filename.endswith(suffix):
        return False
    entity_id = filename[: -len(suffix)]
    try:
        UUID(entity_id)
    except ValueError:
        return False
    return True


def remove_legacy_entity_files(work_dir: Path) -> list[str]:
    """Remove historical entity files from a checked-out working tree."""
    removed: list[str] = []
    for directory, suffix in LEGACY_ENTITY_SUFFIXES.items():
        entity_dir = work_dir / directory
        # Never traverse a checkout-controlled directory symlink: glob would
        # follow it and unlink matching files outside the working tree.
        if entity_dir.is_symlink() or not entity_dir.is_dir():
            continue
        for path in entity_dir.glob(f"*{suffix}"):
            if path.parent != entity_dir:
                continue
            relative_path = path.relative_to(work_dir).as_posix()
            if (path.is_file() or path.is_symlink()) and is_legacy_entity_file(
                relative_path
            ):
                path.unlink()
                removed.append(relative_path)
    return removed
