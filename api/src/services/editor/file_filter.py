"""
File filtering for workspace operations.

Provides consistent filtering of system/metadata files across:
- File uploads (frontend and backend)
- File listings
- File writes

This module is the single source of truth for what files should be
excluded from the workspace.
"""

from pathlib import Path, PureWindowsPath

# Directories to exclude from workspace operations
EXCLUDED_DIRECTORIES = frozenset({
    '.git',
    '__pycache__',
    '.vscode',
    '.idea',
    'node_modules',
    '.venv',
    'venv',
    'env',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    'htmlcov',
    '.tox',
    '.nox',
    '.eggs',
    '*.egg-info',
    '.ipynb_checkpoints',
})

# Files to exclude from workspace operations
EXCLUDED_FILES = frozenset({
    '.DS_Store',
    'Thumbs.db',
    'desktop.ini',
    'bifrost.pyi',
    '.coverage',
    '.python-version',
    '.env',
    '.env.local',
})

# File extensions to exclude
EXCLUDED_EXTENSIONS = frozenset({'.pyc', '.pyo', '.pyd', '.so', '.dylib'})

# Prefixes that indicate hidden/metadata files
EXCLUDED_PREFIXES = ('._',)  # AppleDouble metadata files


def is_excluded_path(path: str | Path) -> bool:
    """
    Check if a path should be excluded from workspace operations.

    Works with both string paths and Path objects.
    Checks each component of the path against exclusion rules.

    Args:
        path: File path to check (relative or absolute)

    Returns:
        True if the path should be excluded, False otherwise
    """
    if isinstance(path, str):
        path = Path(path)

    # Check each component of the path
    for part in path.parts:
        # Skip empty parts and root
        if not part or part == '/':
            continue

        # Check hidden prefixes (AppleDouble files)
        for prefix in EXCLUDED_PREFIXES:
            if part.startswith(prefix):
                return True

        # Check exact matches (files and directories)
        if part in EXCLUDED_FILES or part in EXCLUDED_DIRECTORIES:
            return True

    # Check file extension of the final component
    if path.suffix.lower() in EXCLUDED_EXTENSIONS:
        return True

    return False


def _normalize_workspace_path(
    path: str,
    *,
    allow_root: bool,
    preserve_trailing_slash: bool,
    operation: str,
) -> str:
    """Validate a raw workspace spelling without decoding or normalizing it.

    Workspace object keys are opaque strings. In particular, percent-encoded
    characters must stay opaque rather than being decoded into a traversal
    component before reaching object storage.
    """
    if not isinstance(path, str) or "\x00" in path or "\\" in path:
        raise ValueError(f"Invalid workspace {operation} path")
    if path in {"", "/"}:
        if allow_root:
            return ""
        raise ValueError(f"Workspace {operation} path must name a file or folder")
    if path.startswith("/") or PureWindowsPath(path).drive:
        raise ValueError(f"Workspace {operation} path must be relative")

    trailing_slash = path.endswith("/")
    target = path[:-1] if trailing_slash else path
    if any(part in {"", ".", ".."} for part in target.split("/")):
        raise ValueError(f"Workspace {operation} path must be normalized")
    if is_excluded_path(target):
        raise ValueError("Path is excluded from workspace operations")
    return path if preserve_trailing_slash and trailing_slash else target


def normalize_workspace_delete_path(path: str) -> str:
    """Validate a destructive editor target, allowing one trailing folder slash."""
    return _normalize_workspace_path(
        path,
        allow_root=False,
        preserve_trailing_slash=False,
        operation="delete",
    )


def normalize_workspace_file_path(path: str) -> str:
    """Validate a single workspace file target before reads or writes."""
    target = _normalize_workspace_path(
        path, allow_root=False, preserve_trailing_slash=False, operation="file",
    )
    if path.endswith("/"):
        raise ValueError("Workspace file path must name a file")
    return target


def normalize_workspace_folder_path(path: str) -> str:
    """Validate a folder creation target, allowing one trailing slash."""
    return _normalize_workspace_path(
        path,
        allow_root=False,
        preserve_trailing_slash=False,
        operation="folder",
    )


def normalize_workspace_list_prefix(path: str | None) -> str:
    """Validate an optional workspace listing prefix, including the root."""
    return _normalize_workspace_path(
        "" if path is None else path,
        allow_root=True,
        preserve_trailing_slash=True,
        operation="list",
    )


def is_allowed_path(path: str | Path) -> bool:
    """
    Check if a path is allowed for workspace operations.

    Inverse of is_excluded_path() for convenience.

    Args:
        path: File path to check

    Returns:
        True if the path is allowed, False if it should be excluded
    """
    return not is_excluded_path(path)


# Export constants for use in other modules (e.g., frontend type generation)
def get_exclusion_rules() -> dict:
    """
    Get all exclusion rules as a dictionary.

    Useful for serializing to JSON for frontend consumption.

    Returns:
        Dictionary with all exclusion rules
    """
    return {
        'directories': sorted(EXCLUDED_DIRECTORIES),
        'files': sorted(EXCLUDED_FILES),
        'extensions': sorted(EXCLUDED_EXTENSIONS),
        'prefixes': list(EXCLUDED_PREFIXES),
    }
