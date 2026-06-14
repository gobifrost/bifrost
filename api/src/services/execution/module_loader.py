"""
Module Loader
Pure functions for loading workflows, data providers, and forms at runtime.

Note: Metadata discovery (for DB sync) is handled by FileStorageService at write time.
This module is only for runtime loading of Python code for execution.

Since all workflow/data provider executions run in fresh subprocess workers,
we don't need any module cache clearing - sys.modules starts empty.
"""

import importlib.util
import logging
import os
import sys

from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)


# ==================== VIRTUAL IMPORT NOTE ====================
# Workspace modules are loaded from Redis via virtual_import.py, not from filesystem.
# The virtual import hook must be installed before any workspace imports.
# See: src/services/execution/virtual_import.py
#
# Paths are stored as relative paths (e.g., "features/ticketing/workflow.py")
# and used directly in __file__ attributes for tracebacks.


# ==================== METADATA DATACLASSES ====================
# These are the same as in registry.py but defined here to avoid circular imports


# Type discriminator for all executable types
ExecutableType = Literal["workflow", "tool", "data_provider"]


@dataclass
class WorkflowParameter:
    """Workflow parameter metadata - derived from function signature."""
    name: str
    type: str  # string, int, bool, float, json, list
    label: str | None = None
    required: bool = False
    default_value: Any | None = None
    options: list[dict[str, str]] | None = None  # For Literal types - [{label, value}, ...]


@dataclass
class ExecutableMetadata:
    """
    Base metadata for all executable user code (workflows, tools, data providers).

    This provides common fields that all executable types share.
    Specific types extend this with their own additional fields.
    """
    # Identity
    name: str = ""
    description: str = ""
    category: str = "General"
    tags: list[str] = field(default_factory=list)

    # Type discriminator - determines how this executable is treated
    type: ExecutableType = "workflow"

    # Execution
    timeout_seconds: int = 1800  # Default 30 minutes

    # Source tracking
    source_file_path: str | None = None

    # Parameters and function
    parameters: list[WorkflowParameter] = field(default_factory=list)
    function: Any = None


@dataclass
class WorkflowMetadata(ExecutableMetadata):
    """
    Workflow metadata from @workflow decorator.

    Extends ExecutableMetadata with workflow-specific fields for execution mode,
    scheduling, HTTP endpoints, and tool configuration.
    """
    # Execution mode
    execution_mode: Literal["sync", "async"] = "sync"

    # Retry (for future use)
    retry_policy: dict[str, Any] | None = None

    # Scheduling (for future use)
    schedule: str | None = None

    # HTTP Endpoint Configuration
    endpoint_enabled: bool = False
    allowed_methods: list[str] = field(default_factory=lambda: ["POST"])
    disable_global_key: bool = False
    public_endpoint: bool = False

    # Tool configuration (for AI agent tool calling)
    # Note: When type='tool', this workflow is available as an agent tool
    tool_description: str | None = None  # LLM-friendly description for tool calling

    # Economics - value metrics for reporting
    time_saved: int = 0  # Minutes saved per execution
    value: float = 0.0  # Flexible value unit (e.g., cost savings, revenue)


@dataclass
class DataProviderMetadata(ExecutableMetadata):
    """
    Data provider metadata from @data_provider decorator.

    Extends ExecutableMetadata with data provider-specific fields.
    Data providers return options for form fields and app builder components.
    """
    # Override defaults from base class
    type: ExecutableType = "data_provider"
    timeout_seconds: int = 300  # Data providers have shorter default timeout (5 min)

    # Data provider specific
    cache_ttl_seconds: int = 300  # Default 5 minutes cache

    # Source tracking (home, platform, workspace) - legacy field
    source: Literal["home", "platform", "workspace"] | None = None


# ==================== WORKSPACE HELPERS ====================


def get_workspace_paths() -> list[Path]:
    """
    Get all workspace directories.

    Returns:
        List of Path objects for existing workspace directories
    """
    paths: list[Path] = []
    base_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent

    # Hardcoded workspace path - kept in sync with S3 by WorkspaceSyncService
    workspace_path = Path("/tmp/bifrost/workspace")
    workspace_path.mkdir(parents=True, exist_ok=True)
    paths.append(workspace_path)

    # Platform code directory (always relative to project root)
    platform_path = base_dir / 'platform'
    if platform_path.exists():
        paths.append(platform_path)

    return paths


def import_module(file_path: Path) -> ModuleType:
    """
    Import a Python module from a file path.

    Since workflow executions run in fresh subprocess workers, sys.modules
    starts empty - no cache clearing needed. Python's import machinery
    handles .pyc files correctly (regenerates if stale).

    Args:
        file_path: Path to the Python file to import

    Returns:
        The imported module

    Raises:
        ImportError: If module cannot be imported
    """
    workspace_paths = get_workspace_paths()

    # Calculate module name from workspace-relative path
    module_name = None
    for workspace_path in workspace_paths:
        try:
            relative_path = file_path.relative_to(workspace_path)
            module_parts = list(relative_path.parts[:-1]) + [file_path.stem]
            module_name = '.'.join(module_parts) if module_parts else file_path.stem
            break
        except ValueError:
            continue

    if not module_name:
        module_name = file_path.stem

    # Ensure workspace paths are in sys.path for relative imports
    # (e.g., from modules.helpers import foo)
    for wp in workspace_paths:
        wp_str = str(wp)
        if wp_str not in sys.path:
            sys.path.insert(0, wp_str)

    # Import the module
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if not spec or not spec.loader:
        raise ImportError(f"Could not create module spec for {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        # Clean up on failure
        if module_name in sys.modules:
            del sys.modules[module_name]
        raise ImportError(f"Failed to import {file_path}: {e}") from e

    return module


def exec_from_db(
    code: str,
    path: str,
    function_name: str,
) -> ModuleType:
    """
    Execute workflow code from database and return the module.

    This is the DB-first execution path that replaces file-based imports.
    The code is compiled and executed in a namespace with proper __file__
    and __package__ values for tracebacks and relative imports.

    Args:
        code: The Python source code to execute
        path: The workspace-relative path (e.g., "workflows/process_order.py")
              Used for __file__ injection and traceback filenames
        function_name: The function name to find (for validation)

    Returns:
        A ModuleType object with all defined functions and metadata

    Raises:
        ImportError: If code cannot be compiled or executed
        SyntaxError: If code has syntax errors
    """
    # Use the relative path directly for __file__
    # Tracebacks will show: "features/ticketing/workflow.py", line 42
    file_path_for_traceback = path

    # Calculate module name from path for proper __package__ and relative imports
    path_obj = Path(path)
    module_parts = list(path_obj.parts[:-1]) + [path_obj.stem]
    module_name = '.'.join(module_parts) if module_parts else path_obj.stem

    # Calculate __package__ for relative imports (parent package)
    package_name = '.'.join(path_obj.parts[:-1]) if path_obj.parts[:-1] else None

    # Note: We don't add anything to sys.path here.
    # Workspace modules are loaded via virtual_import.py from Redis,
    # not from the filesystem.

    # Create module object
    module = ModuleType(module_name)
    module.__file__ = file_path_for_traceback
    module.__loader__ = None  # type: ignore[assignment]
    module.__package__ = package_name
    module.__spec__ = None

    # Build execution namespace
    # The namespace includes __builtins__ and module-level attributes
    namespace: dict[str, Any] = {
        "__name__": module_name,
        "__file__": file_path_for_traceback,
        "__package__": package_name,
        "__builtins__": __builtins__,
        "__doc__": None,
        "__loader__": None,
        "__spec__": None,
    }

    # Compile with filename for meaningful stack traces
    try:
        code_obj = compile(code, filename=file_path_for_traceback, mode='exec')
    except SyntaxError as e:
        logger.error(f"Syntax error in workflow code at {path}: {e}")
        raise

    # Execute in the namespace
    try:
        exec(code_obj, namespace)
    except Exception as e:
        logger.error(f"Error executing workflow code at {path}: {e}")
        raise ImportError(f"Failed to execute workflow from DB: {e}") from e

    # Copy namespace to module for attribute access
    for key, value in namespace.items():
        if not key.startswith('__'):
            setattr(module, key, value)

    # Also copy the dunder attributes
    module.__dict__.update(namespace)

    # Register in sys.modules for potential imports
    sys.modules[module_name] = module

    logger.debug(f"Executed workflow from DB: {path} (module: {module_name})")
    return module


def load_workflow_from_db(
    code: str,
    path: str,
    function_name: str,
) -> tuple[Callable[..., Any] | None, WorkflowMetadata | None, str | None]:
    """
    Load a workflow by executing code from the database.

    This is the DB-first loading path. It executes code loaded from file_index/S3
    and extracts the decorated function.

    Args:
        code: Python source code from file_index or S3
        path: Workspace-relative path for __file__ injection
        function_name: Python function name to find (e.g., "get_client_detail")

    Returns:
        Tuple of (callable, metadata, error_message):
        - On success: (callable, metadata, None)
        - On failure: (None, None, error_message)
    """
    try:
        module = exec_from_db(code=code, path=path, function_name=function_name)
    except (SyntaxError, ImportError) as e:
        logger.error(f"Failed to execute workflow from DB: {e}")
        user_friendly_error = (
            "An unexpected error happened outside of your workflow's function. "
            "This could be because you used an invalid property on a decorator, "
            f"import or something else.\n\n{e}"
        )
        return None, None, user_friendly_error

    # Find the decorated function by name
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if not callable(attr):
            continue

        # All decorators use _executable_metadata
        # Match by Python function name (attr_name), not display name (metadata.name)
        if hasattr(attr, '_executable_metadata') and attr_name == function_name:
            metadata = getattr(attr, '_executable_metadata', None)
            if metadata:
                # For data providers, convert to WorkflowMetadata for consistent execution
                if hasattr(metadata, 'type') and metadata.type == 'data_provider':
                    workflow_meta = WorkflowMetadata(
                        name=metadata.name,
                        description=metadata.description,
                        category=getattr(metadata, 'category', 'General'),
                        parameters=getattr(metadata, 'parameters', []),
                        timeout_seconds=getattr(metadata, 'timeout_seconds', 300),
                        type='data_provider',
                    )
                    return (attr, workflow_meta, None)
                elif isinstance(metadata, WorkflowMetadata):
                    return (attr, metadata, None)
                else:
                    return (attr, _convert_workflow_metadata(metadata), None)

    logger.warning(f"Workflow function '{function_name}' not found in code from {path}")
    return (None, None, f"Workflow function '{function_name}' not found in code from {path}")


# ==================== WORKFLOW DISCOVERY ====================


def scan_all_workflows() -> list[WorkflowMetadata]:
    """
    Scan all workspace directories and return workflow metadata.

    Imports each Python file and extracts workflows/tools with
    the _executable_metadata attribute set by @workflow or @tool decorator.

    Returns:
        List of WorkflowMetadata objects (including type='workflow' and type='tool')
    """
    workflows: list[WorkflowMetadata] = []
    workspace_paths = get_workspace_paths()

    if not workspace_paths:
        logger.warning("No workspace paths found")
        return workflows

    for workspace_path in workspace_paths:
        for py_file in workspace_path.rglob("*.py"):
            # Skip __init__.py and private files
            if py_file.name.startswith("_"):
                continue
            # Skip .packages directory
            if ".packages" in py_file.parts:
                continue

            try:
                module = import_module(py_file)

                # Scan module for decorated functions
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if callable(attr) and hasattr(attr, '_executable_metadata'):
                        metadata = getattr(attr, '_executable_metadata', None)
                        if metadata is None:
                            continue
                        # Only include workflows and tools, not data providers
                        if hasattr(metadata, 'type') and metadata.type == 'data_provider':
                            continue
                        if isinstance(metadata, WorkflowMetadata):
                            workflows.append(metadata)
                        else:
                            # Convert from old registry type if needed
                            workflows.append(_convert_workflow_metadata(metadata))

            except Exception as e:
                logger.warning(f"Failed to scan {py_file}: {e}")

    logger.info(f"Scanned {len(workflows)} workflows from {len(workspace_paths)} workspace(s)")
    return workflows


def load_workflow(name: str) -> tuple[Callable, WorkflowMetadata] | None:
    """
    Find and load a specific workflow by name.

    Scans workspace directories, imports, and returns the
    function and metadata for the named workflow/tool.

    Args:
        name: Workflow name to find

    Returns:
        Tuple of (function, metadata) or None if not found
    """
    workspace_paths = get_workspace_paths()

    if not workspace_paths:
        return None

    for workspace_path in workspace_paths:
        for py_file in workspace_path.rglob("*.py"):
            if py_file.name.startswith("_"):
                continue
            if ".packages" in py_file.parts:
                continue

            try:
                module = import_module(py_file)

                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if callable(attr) and hasattr(attr, '_executable_metadata'):
                        metadata = getattr(attr, '_executable_metadata', None)
                        if metadata and hasattr(metadata, 'name') and metadata.name == name:
                            if isinstance(metadata, WorkflowMetadata):
                                return (attr, metadata)
                            else:
                                return (attr, _convert_workflow_metadata(metadata))

            except Exception as e:
                logger.debug(f"Error scanning {py_file} for workflow '{name}': {e}")

    return None


# ==================== DATA PROVIDER DISCOVERY ====================


def scan_all_data_providers() -> list[DataProviderMetadata]:
    """
    Scan all workspace directories and return data provider metadata.

    Returns:
        List of DataProviderMetadata objects
    """
    providers: list[DataProviderMetadata] = []
    workspace_paths = get_workspace_paths()

    if not workspace_paths:
        return providers

    for workspace_path in workspace_paths:
        for py_file in workspace_path.rglob("*.py"):
            if py_file.name.startswith("_"):
                continue
            if ".packages" in py_file.parts:
                continue

            try:
                module = import_module(py_file)

                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if callable(attr) and hasattr(attr, '_executable_metadata'):
                        metadata = getattr(attr, '_executable_metadata', None)
                        if metadata is None:
                            continue
                        # Only include data providers
                        if not hasattr(metadata, 'type') or metadata.type != 'data_provider':
                            continue
                        if isinstance(metadata, DataProviderMetadata):
                            providers.append(metadata)
                        else:
                            providers.append(_convert_data_provider_metadata(metadata))

            except Exception as e:
                logger.warning(f"Failed to scan {py_file}: {e}")

    logger.info(f"Scanned {len(providers)} data providers from {len(workspace_paths)} workspace(s)")
    return providers


def load_data_provider(name: str) -> tuple[Callable, DataProviderMetadata] | None:
    """
    Find and load a specific data provider by name.

    Args:
        name: Data provider name to find

    Returns:
        Tuple of (function, metadata) or None if not found
    """
    workspace_paths = get_workspace_paths()

    if not workspace_paths:
        return None

    for workspace_path in workspace_paths:
        for py_file in workspace_path.rglob("*.py"):
            if py_file.name.startswith("_"):
                continue
            if ".packages" in py_file.parts:
                continue

            try:
                module = import_module(py_file)

                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if callable(attr) and hasattr(attr, '_executable_metadata'):
                        metadata = getattr(attr, '_executable_metadata', None)
                        # Only match data providers
                        if metadata and hasattr(metadata, 'type') and metadata.type == 'data_provider':
                            if hasattr(metadata, 'name') and metadata.name == name:
                                if isinstance(metadata, DataProviderMetadata):
                                    return (attr, metadata)
                                else:
                                    return (attr, _convert_data_provider_metadata(metadata))

            except Exception as e:
                logger.debug(f"Error scanning {py_file} for data provider '{name}': {e}")

    return None


# ==================== METADATA CONVERSION HELPERS ====================
# These handle compatibility with existing decorator output format


def _convert_workflow_metadata(old_metadata: Any) -> WorkflowMetadata:
    """Convert old registry WorkflowMetadata to discovery WorkflowMetadata."""
    # Determine type from legacy 'tool' field or new 'type' field
    is_tool = getattr(old_metadata, 'tool', False) or getattr(old_metadata, 'is_tool', False)
    executable_type = getattr(old_metadata, 'type', 'tool' if is_tool else 'workflow')

    return WorkflowMetadata(
        name=old_metadata.name,
        description=old_metadata.description,
        category=getattr(old_metadata, 'category', 'General'),
        tags=getattr(old_metadata, 'tags', []),
        type=executable_type,
        timeout_seconds=getattr(old_metadata, 'timeout_seconds', 1800),
        source_file_path=getattr(old_metadata, 'source_file_path', None),
        parameters=_convert_parameters(getattr(old_metadata, 'parameters', [])),
        function=getattr(old_metadata, 'function', None),
        execution_mode=getattr(old_metadata, 'execution_mode', 'sync'),
        retry_policy=getattr(old_metadata, 'retry_policy', None),
        schedule=getattr(old_metadata, 'schedule', None),
        endpoint_enabled=getattr(old_metadata, 'endpoint_enabled', False),
        allowed_methods=getattr(old_metadata, 'allowed_methods', ['POST']),
        disable_global_key=getattr(old_metadata, 'disable_global_key', False),
        public_endpoint=getattr(old_metadata, 'public_endpoint', False),
        tool_description=getattr(old_metadata, 'tool_description', None),
        time_saved=getattr(old_metadata, 'time_saved', 0),
        value=getattr(old_metadata, 'value', 0.0),
    )


def _convert_data_provider_metadata(old_metadata: Any) -> DataProviderMetadata:
    """Convert old registry DataProviderMetadata to discovery DataProviderMetadata."""
    return DataProviderMetadata(
        name=old_metadata.name,
        description=old_metadata.description,
        category=getattr(old_metadata, 'category', 'General'),
        tags=getattr(old_metadata, 'tags', []),
        type="data_provider",
        timeout_seconds=getattr(old_metadata, 'timeout_seconds', 300),
        source_file_path=getattr(old_metadata, 'source_file_path', None),
        parameters=_convert_parameters(getattr(old_metadata, 'parameters', [])),
        function=getattr(old_metadata, 'function', None),
        cache_ttl_seconds=getattr(old_metadata, 'cache_ttl_seconds', 300),
        source=getattr(old_metadata, 'source', None),
    )


def _convert_parameters(params: list) -> list[WorkflowParameter]:
    """Convert parameter list to WorkflowParameter list."""
    result = []
    for p in params:
        if isinstance(p, WorkflowParameter):
            result.append(p)
        else:
            result.append(WorkflowParameter(
                name=p.name,
                type=p.type,
                label=getattr(p, 'label', None),
                required=getattr(p, 'required', False),
                default_value=getattr(p, 'default_value', None),
            ))
    return result


