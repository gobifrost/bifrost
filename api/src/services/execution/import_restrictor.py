"""
Import Restriction System for Workspace Code Isolation

Prevents workspace code from importing engine internals while allowing
access to whitelisted public API modules.

This module implements a MetaPathFinder that intercepts import statements
and blocks workspace code from importing engine modules, except for explicitly
whitelisted shared exports.

Usage:
    from src.services.execution.import_restrictor import install_import_restrictions
    import os

    WORKSPACE_PATH = os.path.join(os.path.dirname(__file__), '..', 'workspace')
    install_import_restrictions([WORKSPACE_PATH])

    # Now workspace code imports are restricted
"""

import inspect
import logging
import os
import sys
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from typing import Any

logger = logging.getLogger(__name__)


class WorkspaceImportRestrictor(MetaPathFinder):
    """
    Meta path finder that blocks workspace code from importing internal modules.

    Blocked prefixes: src.* (except whitelisted SDK exports)
    Allowed exports: bifrost.*, src.sdk.*, src.models.models (for type hints)
    """

    # T028: Define blocked import prefixes
    BLOCKED_PREFIXES: tuple[str, ...] = ('src.',)

    # T029: Define whitelisted exports (public API)
    # These are the ONLY src.* modules that user workflow code can import
    ALLOWED_EXPORTS: set[str] = {
        # Bifrost SDK - Public API for user code
        'bifrost',
        'bifrost._context',
        'bifrost._internal',
        'bifrost._logging',
        'bifrost._sync',
        'bifrost._write_buffer',
        'bifrost.organizations',
        'bifrost.workflows',
        'bifrost.files',
        'bifrost.forms',
        'bifrost.events',
        'bifrost.executions',
        'bifrost.roles',
        'bifrost.config',
        'bifrost.secrets',
        'bifrost.integrations',
        # SDK Contract - src modules that bifrost SDK imports
        'src.sdk.decorators',
        'src.sdk.context',
        'src.sdk.errors',
        'src.sdk.error_handling',
        # Models (for type hints in user code)
        'src.models.models',
        'src.models.enums',
    }

    def __init__(self, workspace_paths: list[str], home_path: str | None = None) -> None:
        """
        Initialize restrictor with workspace paths.

        Args:
            workspace_paths: List of absolute directory paths considered "workspace code"
            home_path: Optional absolute path to user code directory (/home) for stricter restrictions

        Raises:
            ValueError: If any workspace path is not absolute
        """
        # Validate all paths are absolute
        for path in workspace_paths:
            if not os.path.isabs(path):
                raise ValueError(
                    f"Workspace path must be absolute: {path}. "
                    f"Use os.path.abspath() or Path.resolve()"
                )

        self.workspace_paths = [os.path.normpath(p) for p in workspace_paths]
        self.home_path = os.path.normpath(home_path) if home_path else None
        logger.info(
            f"Import restrictor initialized with workspace paths: {self.workspace_paths}, "
            f"home path: {self.home_path}"
        )

    # T030: Implement find_spec() with stack inspection
    def find_spec(
        self,
        fullname: str,
        path: Any | None = None,
        target: Any | None = None
    ) -> ModuleSpec | None:
        """
        Check if import should be blocked.

        Called automatically by Python's import system.

        Args:
            fullname: Fully qualified module name (e.g., "engine.shared.storage")
            path: Module search path
            target: Target module (optional)

        Returns:
            None: Allow import to proceed (return None, don't block)

        Raises:
            ImportError: If workspace code attempts to import blocked module
        """
        # Check if this is a blocked import
        if not self._is_blocked_import(fullname):
            return None  # Not blocked, allow import

        # Check if this is an allowed whitelisted export
        if fullname in self.ALLOWED_EXPORTS:
            return None  # Whitelisted, allow import

        # Inspect call stack to determine if caller is workspace code
        caller_info = self._get_caller_info()
        if not caller_info:
            return None  # Not from workspace, allow import

        caller_path, is_home = caller_info

        # /home code has stricter restrictions - can only import whitelisted modules
        # /platform code can import from src.* (platform workflows need internal modules)
        if is_home:
            # /home code tried to import a blocked module that's not whitelisted
            self._raise_import_error(fullname, caller_path, is_home=True)
        else:
            # /platform code - allow src.* imports for platform workflows
            return None

    def _is_blocked_import(self, module_name: str) -> bool:
        """Check if module name matches blocked prefixes"""
        return any(module_name.startswith(prefix) for prefix in self.BLOCKED_PREFIXES)

    def _get_caller_info(self) -> tuple[str, bool] | None:
        """
        Inspect call stack to determine if import originated from workspace code.

        Returns:
            Tuple of (caller_filepath, is_home_code) if from workspace, None otherwise
            - caller_filepath: The absolute path to the file containing the import
            - is_home_code: True if from /home, False if from /platform

        Note: We only check the immediate caller (the frame that contains the import
        statement), not the entire call chain. This allows platform modules like bifrost
        to import from src.* even when called from /home code.
        """
        # Get current call stack
        try:
            stack = inspect.stack()
        except KeyError:
            # During app initialization, sys.modules['__main__'] may not exist
            # In this case, we're definitely not in workspace code (we're in startup)
            return None

        # Find the frame that contains the actual import statement
        # Skip frames from the import system itself and this restrictor
        for frame_info in stack:
            filename = frame_info.filename

            # Skip frames from this restrictor module
            if 'import_restrictor' in filename:
                continue

            # Skip frames from Python's import machinery
            if '<frozen importlib' in filename or 'importlib' in filename:
                continue

            # This is the actual import statement - check if it's in workspace
            normalized_path = os.path.normpath(os.path.abspath(filename))

            if self._is_workspace_code(normalized_path):
                # Determine if it's from /home (stricter) or /platform (more permissive)
                is_home = self._is_home_code(normalized_path)
                return (normalized_path, is_home)

        return None

    def _is_workspace_code(self, filepath: str) -> bool:
        """
        Check if a file path is within workspace directories.

        Args:
            filepath: Absolute file path to check

        Returns:
            True if filepath is under any workspace path
        """
        normalized = os.path.normpath(os.path.abspath(filepath))

        for workspace_path in self.workspace_paths:
            # Check if file is under this workspace path
            try:
                rel_path = os.path.relpath(normalized, workspace_path)
                # If relative path doesn't start with '..', it's inside workspace
                if not rel_path.startswith('..'):
                    return True
            except ValueError:
                # Different drives on Windows - not in workspace
                continue

        return False

    def _is_home_code(self, filepath: str) -> bool:
        """
        Check if a file path is within /home directory (user code).

        Args:
            filepath: Absolute file path to check

        Returns:
            True if filepath is under /home path
        """
        if not self.home_path:
            return False

        normalized = os.path.normpath(os.path.abspath(filepath))

        try:
            rel_path = os.path.relpath(normalized, self.home_path)
            # If relative path doesn't start with '..', it's inside /home
            return not rel_path.startswith('..')
        except ValueError:
            # Different drives on Windows - not in /home
            return False

    def _raise_import_error(self, module_name: str, caller_path: str, is_home: bool) -> None:
        """
        Raise ImportError with helpful guidance for developers.

        Args:
            module_name: The blocked module that was attempted
            caller_path: Path to the file attempting the import
            is_home: True if from /home, False if from /platform

        Raises:
            ImportError: Always raises with clear guidance
        """
        if is_home:
            # /home code error message - can only use bifrost SDK
            error_msg = (
                f"User code in /home cannot import '{module_name}'. "
                f"\n\nUser code can only import from the Bifrost SDK:\n"
                f"  - from bifrost import organizations  # Organization management\n"
                f"  - from bifrost import workflows       # Workflow execution\n"
                f"  - from bifrost import files           # File operations\n"
                f"  - from bifrost import forms           # Form management\n"
                f"  - from bifrost import executions      # Execution history\n"
                f"  - from bifrost import roles           # Role management\n"
                f"  - from bifrost import config          # Configuration access\n"
                f"  - from bifrost import secrets         # Secrets management\n"
                f"  - from bifrost import integrations    # Integration and OAuth access\n"
                f"\n"
                f"The bifrost SDK provides a safe, stable API for accessing platform features.\n"
                f"Direct imports from src.* modules are not allowed.\n"
                f"\n"
                f"See documentation at /docs/bifrost-sdk.md for the complete SDK reference."
            )
        else:
            # /platform code error message - platform workflows can use SDK modules
            error_msg = (
                f"Platform code in /platform cannot import '{module_name}'. "
                f"\n\nPlatform workflows should use the Bifrost SDK for most operations.\n"
                f"The src.* modules are internal implementation details."
            )

        error_msg = f"{error_msg}\n\nImport attempted from: {caller_path}"

        # T036: Log the violation attempt to audit log for security monitoring
        logger.warning(
            f"Import restriction violated: {module_name} from {caller_path} (is_home={is_home})",
            extra={
                'blocked_module': module_name,
                'workspace_file': caller_path,
                'is_home_code': is_home,
                'event_type': 'import_violation_attempt'
            }
        )

        raise ImportError(error_msg)


# T032: Install function for convenience
def install_import_restrictions(
    workspace_paths: list[str],
    home_path: str | None = None
) -> None:
    """
    Install import restrictions before workspace code discovery.

    Must be called in function_app.py before importing any workspace modules.

    Args:
        workspace_paths: List of absolute paths to workspace directories (/home, /platform)
        home_path: Optional absolute path to /home directory for stricter restrictions

    Raises:
        ValueError: If workspace_paths contains relative paths

    Example:
        import os
        from src.services.execution.import_restrictor import install_import_restrictions

        HOME_PATH = os.path.join(os.path.dirname(__file__), 'home')
        PLATFORM_PATH = os.path.join(os.path.dirname(__file__), 'platform')
        install_import_restrictions(
            [os.path.abspath(HOME_PATH), os.path.abspath(PLATFORM_PATH)],
            home_path=os.path.abspath(HOME_PATH)
        )
    """
    if not workspace_paths:
        raise ValueError("At least one workspace path must be provided")

    # Create and install restrictor
    restrictor = WorkspaceImportRestrictor(workspace_paths, home_path=home_path)

    # Add to sys.meta_path (import hook system)
    sys.meta_path.insert(0, restrictor)

    logger.info(
        f"Import restrictions installed for {len(workspace_paths)} workspace path(s), "
        f"home_path={home_path}"
    )


def remove_import_restrictions() -> None:
    """
    Remove import restrictions (useful for testing).

    Removes all WorkspaceImportRestrictor instances from sys.meta_path.
    """
    sys.meta_path = [
        finder for finder in sys.meta_path
        if not isinstance(finder, WorkspaceImportRestrictor)
    ]
    logger.info("Import restrictions removed")


def get_active_restrictors() -> list[WorkspaceImportRestrictor]:
    """
    Get list of active import restrictors.

    Returns:
        List of WorkspaceImportRestrictor instances on sys.meta_path
    """
    return [
        finder for finder in sys.meta_path
        if isinstance(finder, WorkspaceImportRestrictor)
    ]
