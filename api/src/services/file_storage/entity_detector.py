"""Entity type detection for platform files.

Detects whether a file should be stored in the database (platform entities)
or in S3 (regular files).

Platform entities:
- Workflows (.py with @workflow decorator)
- Data providers (.py with @data_provider decorator)

Forms and agents are imported from the manifest, not per-file YAML.

Regular files go to S3.
"""

import ast
from dataclasses import dataclass
from typing import Any


@dataclass
class PythonEntityDetectionResult:
    """Result of Python entity detection, including cached AST for reuse."""
    entity_type: str | None  # "workflow", "module", or None
    ast_tree: ast.Module | None  # Parsed AST tree (None if parse failed or not needed)
    content_str: str | None  # Decoded content string
    has_decorators: bool = False  # True if file has @workflow/@data_provider/@tool decorators
    syntax_error: str | None = None  # Syntax error message if parse failed


def detect_platform_entity_type(
    path: str,
    content: bytes,
    app_prefixes: set[str] | None = None,
) -> str | None:
    """
    Detect if a file is a platform entity that should be stored in the database.

    Platform entities are stored in the database, not S3:
    - Workflows (.py with @workflow decorator): stored in file_index + _repo/ S3
    - Data providers (.py with @data_provider decorator): stored in file_index + _repo/ S3

    Forms and agents are managed via the manifest (.bifrost/forms.yaml,
    .bifrost/agents.yaml) — they are not detected from per-file YAML.

    Regular files (modules, data files, configs) go to S3.

    Args:
        path: File path
        content: File content
        app_prefixes: Optional set of known app repo_path prefixes (e.g. {"apps/my-app", "custom/app"}).
            If provided, paths under these prefixes are detected as "app_file".

    Returns:
        Entity type ("workflow", "app_file") or None for regular files
    """
    # App files: check against known app repo_path prefixes from DB
    if app_prefixes:
        for prefix in app_prefixes:
            normalized = prefix.rstrip("/") + "/"
            if path.startswith(normalized):
                return "app_file"

    # Python files - check for SDK decorators
    if path.endswith(".py"):
        return detect_python_entity_type(content)

    # Text/documentation files - stored in workspace_files.content
    text_extensions = (".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".ini", ".cfg")
    if path.endswith(text_extensions):
        return "text"

    # Regular file - goes to S3
    return None


def detect_python_entity_type(content: bytes) -> str | None:
    """
    Check if Python content has SDK decorators (@workflow, @data_provider).

    Simple wrapper around detect_python_entity_type_with_ast that returns
    just the entity type string for backward compatibility.

    Args:
        content: Python file content

    Returns:
        "workflow" if SDK decorators found, "module" for other Python files
    """
    result = detect_python_entity_type_with_ast(content)
    return result.entity_type


def detect_python_entity_type_with_ast(content: bytes) -> PythonEntityDetectionResult:
    """
    Check if Python content has SDK decorators, returning cached AST for reuse.

    Uses fast regex check first, then AST verification only if needed.
    For modules without decorators, skips AST parsing entirely to save memory
    (~100MB for a 4MB Python file).

    Args:
        content: Python file content

    Returns:
        PythonEntityDetectionResult with entity_type, ast_tree, content_str,
        has_decorators flag, and syntax_error if applicable
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        content_str = content.decode("utf-8", errors="replace")
    except Exception:
        # Non-decodable content - not a valid Python module
        return PythonEntityDetectionResult(
            entity_type=None,
            ast_tree=None,
            content_str=None,
            has_decorators=False,
        )

    # Fast regex check - if no decorator-like patterns, it's a module
    # SKIP AST parsing entirely - modules don't need indexing
    if (
        "@workflow" not in content_str
        and "@data_provider" not in content_str
        and "@tool" not in content_str
        and "@service" not in content_str
    ):
        logger.info(f"Skipping AST parse - no decorator patterns found (content size: {len(content_str)} bytes)")
        return PythonEntityDetectionResult(
            entity_type="module",
            ast_tree=None,  # No AST needed for plain modules
            content_str=content_str,
            has_decorators=False,
        )

    # Has decorator-like patterns - need AST to verify they're actual decorators
    try:
        tree = ast.parse(content_str)
    except SyntaxError as e:
        # Syntax error - store as module, propagate error info
        error_msg = f"{e.msg}" if e.msg else str(e)
        logger.warning(f"Syntax error during entity detection: {error_msg}")
        return PythonEntityDetectionResult(
            entity_type="module",
            ast_tree=None,
            content_str=content_str,
            has_decorators=True,  # Had patterns, couldn't verify
            syntax_error=error_msg,
        )

    # Walk AST to verify decorators are actually used on functions
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for decorator in node.decorator_list:
            decorator_info = _parse_decorator(decorator)
            if decorator_info:
                decorator_name, _ = decorator_info
                if decorator_name in ("workflow", "data_provider", "tool", "service"):
                    return PythonEntityDetectionResult(
                        entity_type="workflow",
                        ast_tree=tree,
                        content_str=content_str,
                        has_decorators=True,
                    )

    # Has @workflow/@data_provider/@tool in content but not as actual decorators
    # (e.g., in comments, strings, or as variable names) - treat as module
    # Keep AST since we already parsed it and it might be useful
    return PythonEntityDetectionResult(
        entity_type="module",
        ast_tree=tree,
        content_str=content_str,
        has_decorators=False,
    )


def _parse_decorator(decorator: ast.AST) -> tuple[str, dict[str, Any]] | None:
    """
    Parse a decorator AST node to extract name and keyword arguments.

    Returns:
        Tuple of (decorator_name, kwargs_dict) or None if not a workflow/provider decorator
    """
    # Handle @workflow (no parentheses)
    if isinstance(decorator, ast.Name):
        if decorator.id in ("workflow", "tool", "data_provider", "service"):
            return decorator.id, {}
        return None

    # Handle @workflow(...) (with parentheses)
    if isinstance(decorator, ast.Call):
        if isinstance(decorator.func, ast.Name):
            decorator_name = decorator.func.id
        elif isinstance(decorator.func, ast.Attribute):
            # Handle module.workflow (e.g., bifrost.workflow)
            decorator_name = decorator.func.attr
        else:
            return None

        if decorator_name not in ("workflow", "tool", "data_provider", "service"):
            return None

        # Extract keyword arguments
        kwargs = {}
        for keyword in decorator.keywords:
            if keyword.arg:
                value = _ast_value_to_python(keyword.value)
                kwargs[keyword.arg] = value

        return decorator_name, kwargs

    return None


def _ast_value_to_python(node: ast.AST) -> Any:
    """Convert an AST node to a Python value."""
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.List):
        return [_ast_value_to_python(elt) for elt in node.elts]
    elif isinstance(node, ast.Dict):
        return {
            _ast_value_to_python(k): _ast_value_to_python(v)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }
    elif isinstance(node, ast.Name):
        # Handle True, False, None
        if node.id == "True":
            return True
        elif node.id == "False":
            return False
        elif node.id == "None":
            return None
    return None
