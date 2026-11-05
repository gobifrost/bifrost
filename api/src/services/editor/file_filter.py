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


def normalize_workspace_delete_path(path: str) -> str:
    """Validate a destructive editor target, allowing one trailing folder slash.

    Inspect the raw spelling before pathlib can collapse empty/dot components.
    Root listings are valid for reads, but must never be deletion targets.
    """
    if "\x00" in path or "\\" in path:
        raise ValueError("Invalid workspace delete path")
    if path.startswith("/") or PureWindowsPath(path).drive:
        raise ValueError("Workspace delete path must be relative")

    target = path.removesuffix("/")
    if any(part in {"", ".", ".."} for part in target.split("/")):
        raise ValueError("Workspace delete path must name a normalized file or folder")
    if is_excluded_path(target):
        raise ValueError("Path is excluded from workspace operations")
    return target


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
