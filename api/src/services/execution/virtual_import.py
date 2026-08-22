"""
Virtual Module Import System

Loads Python modules from Redis cache instead of filesystem.
Follows the MetaPathFinder pattern from import_restrictor.py.

This allows workers to load workspace modules without needing
the actual files synced to disk - everything comes from Redis cache.

Usage:
    from src.services.execution.virtual_import import install_virtual_import_hook
    install_virtual_import_hook()

    # Now workspace imports are loaded from Redis
    from shared import halopsa  # Loaded from Redis, not filesystem

IMPORTANT: This module must be careful about imports and Redis calls
during find_spec() because the import system itself may trigger imports
(e.g., socket.getaddrinfo imports encodings.idna). We use:
1. A thread-local recursion guard to prevent infinite recursion
2. Early exit for known stdlib module prefixes
"""

import logging
import sys
import threading
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path
from types import ModuleType
from typing import Any

from src.core.module_cache_sync import (
    resolve_module_sync,
)

logger = logging.getLogger(__name__)

# Thread-local storage for recursion guard
_thread_local = threading.local()

# Standard library module prefixes that we should NEVER try to load from Redis.
# These modules are needed by Python's import system itself or by Redis client.
# Adding to this list prevents infinite recursion.
STDLIB_PREFIXES = frozenset([
    "encodings",  # Used by socket.getaddrinfo for hostname resolution
    "codecs",     # Used by encodings
    "_",          # All C extension modules (_socket, _ssl, etc.)
    "builtins",
    "sys",
    "importlib",
    "abc",
    "io",
    "os",
    "posix",
    "errno",
    "socket",
    "ssl",
    "select",
    "selectors",
    "threading",
    "concurrent",
    "asyncio",
    "redis",      # Redis library itself
    "json",       # Used to deserialize cached modules
    "functools",  # Used by lru_cache in module_cache_sync
    "typing",
    "collections",
    "logging",
    "warnings",
    "traceback",
    "linecache",
    "tokenize",
    "re",
    "sre_compile",
    "sre_parse",
    "sre_constants",
    "stringprep",  # Used by encodings.idna
    "copyreg",
    "copy",
    "types",
    "weakref",
    "contextlib",
    "dataclasses",
    "enum",
    "atexit",
    "signal",
    "time",
    "datetime",
    "calendar",
    "locale",
    "struct",
    "decimal",
    "numbers",
    "fractions",
    "random",
    "hashlib",
    "hmac",
    "secrets",
    "base64",
    "binascii",
    "urllib",
    "http",
    "email",
    "html",
    "mimetypes",
    "pathlib",
    "fnmatch",
    "glob",
    "shutil",
    "stat",
    "fileinput",
    "tempfile",
    "zipfile",
    "gzip",
    "bz2",
    "lzma",
    "tarfile",
    "csv",
    "configparser",
    "pickle",
    "marshal",
    "shelve",
    "dbm",
    "sqlite3",
    "zlib",
    "platform",
    "ctypes",
    "multiprocessing",
    "subprocess",
    "queue",
    "heapq",
    "bisect",
    "array",
    "operator",
    "itertools",
    "gettext",
    "argparse",
    "uuid",
    "ipaddress",
    "unittest",
    "pydantic",
    "sqlalchemy",
    "alembic",
    "pika",
    "aio_pika",
    "aiormq",
    "httpx",
    "anyio",
    "sniffio",
    "certifi",
    "charset_normalizer",
    "idna",
    "requests",
    "starlette",
    "fastapi",
    "uvicorn",
    "pytest",
    # Bifrost runtime packages are platform code, not workspace import roots.
    # Let PathFinder resolve them without asking the module API first.
    "bifrost",
    "src",
])

_APP_ROOT = Path(__file__).resolve().parents[3]
_BIFROST_SOURCE_ROOTS = (
    _APP_ROOT / "src",
    _APP_ROOT / "shared",
    _APP_ROOT / "bifrost",
)



class NamespacePackageLoader(Loader):
    """
    Loader for namespace packages (directories without __init__.py).

    Creates an empty module with __path__ set so submodule imports work.
    This enables PEP 420 namespace packages for virtual imports, allowing
    users to organize modules in folders without requiring __init__.py files.

    Example:
        If Redis cache has "modules/extensions/halopsa.py" but no
        "modules/__init__.py" or "modules/extensions/__init__.py",
        both "modules" and "modules.extensions" become namespace packages.
    """

    def __init__(self, path: str):
        """
        Initialize with the directory path.

        Args:
            path: Directory path (e.g., "modules" or "modules/extensions")
        """
        self.path = path

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        """Return None to use default module creation semantics."""
        return None

    def exec_module(self, module: ModuleType) -> None:
        """
        Initialize the namespace package module.

        Namespace packages have no code to execute - just set __path__
        for submodule resolution and mark as having no file.
        """
        module.__path__ = [self.path]
        module.__file__ = None  # Namespace packages have no file
        module.__loader__ = self


class VirtualModuleLoader(Loader):
    """
    Loads module content from cached source code.

    Compiles and executes Python code in the module's namespace,
    setting __file__ to the relative path for meaningful tracebacks.
    """

    def __init__(
        self,
        path: str,
        content: str,
        is_package: bool = False,
        content_hash: str = "",
        storage_path: str | None = None,
    ):
        """
        Initialize loader with module content.

        Args:
            path: Relative file path (e.g., "shared/halopsa.py")
            content: Python source code
            is_package: True if this is a package (__init__.py)
            content_hash: SHA-256 hash of content for change detection
            storage_path: Exact Solution or global repository cache path
        """
        self.path = path
        self.content = content
        self.is_package = is_package
        self.content_hash = content_hash
        self.storage_path = storage_path or path

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        """Return None to use default module creation semantics."""
        return None

    def exec_module(self, module: ModuleType) -> None:
        """Execute the module code in the module's namespace."""
        # Use relative path directly for __file__ - no virtual prefix needed
        # Tracebacks will show: "shared/halopsa.py", line 42
        module.__file__ = self.path
        module.__loader__ = self
        module.__content_hash__ = self.content_hash
        module.__storage_path__ = self.storage_path

        if self.is_package:
            # Packages need __path__ for submodule imports
            # Use the directory portion of the relative path
            module.__path__ = [str(Path(self.path).parent)]

        # Compile and execute
        try:
            code = compile(self.content, filename=self.path, mode="exec")
            exec(code, module.__dict__)
        except SyntaxError as e:
            logger.error(f"Syntax error in virtual module {self.path}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error executing virtual module {self.path}: {e}")
            raise


class VirtualModuleFinder(MetaPathFinder):
    """
    Meta path finder that loads workspace modules from Redis cache.

    Converts Python module names to file paths and asks the API-backed targeted
    resolver whether the name is a workspace module, package, namespace, or miss.

    Key design points:
    - Installed dependencies get first refusal through PathFinder
    - Workspace code still wins over accidental same-named platform source files
    - The targeted resolver is the source of truth for workspace ownership
    - Non-workspace third-party imports fall through to normal import handling
    - Supports both modules (.py) and packages (__init__.py)
    """

    def find_spec(
        self,
        fullname: str,
        path: Any | None = None,
        target: Any | None = None,
    ) -> ModuleSpec | None:
        """
        Find module spec for a given module name.

        This is called by Python's import system for every import.
        We check if the module exists in our Redis cache and return
        a spec with our custom loader if found.

        IMPORTANT: This method must be careful to avoid recursion.
        Redis calls may trigger imports (e.g., socket -> encodings.idna),
        which would call find_spec again. We use:
        1. A recursion guard to prevent re-entrant calls during Redis ops
        2. Early exit for stdlib modules that could never be workspace code

        Args:
            fullname: Fully qualified module name (e.g., "shared.halopsa")
            path: Module search path (ignored, we use our cache)
            target: Target module (optional, rarely used)

        Returns:
            ModuleSpec if module is in our cache, None otherwise
            (None tells Python to try the next finder)
        """
        # Fast path: skip stdlib/3rd-party modules that can't be workspace code
        # This also prevents recursion since Redis client imports these
        top_level = fullname.split(".")[0]
        if top_level in STDLIB_PREFIXES:
            return None

        # Recursion guard: if we're already in find_spec (e.g., Redis triggered
        # an import), return None to let the normal import system handle it
        if getattr(_thread_local, "in_find_spec", False):
            return None

        # Set recursion guard
        _thread_local.in_find_spec = True
        try:
            return self._find_spec_impl(fullname, path)
        finally:
            _thread_local.in_find_spec = False

    def _find_spec_impl(self, fullname: str, path: Any | None = None) -> ModuleSpec | None:
        """
        Internal implementation of find_spec.

        Separated from find_spec to keep the recursion guard clean.

        Installed dependencies are allowed to resolve before workspace code.
        A source file merely present on Bifrost's application path does not get
        that privilege: the targeted resolver must first be allowed to claim a
        same-named workspace package, preserving the collision fix from #419.
        """
        local_spec = PathFinder.find_spec(fullname, path)
        if local_spec is not None and not _is_bifrost_source_spec(local_spec):
            return None

        resolution = resolve_module_sync(fullname)
        if resolution.kind in {"module", "package"} and resolution.content is not None:
            is_package = resolution.kind == "package"
            loader = VirtualModuleLoader(
                resolution.path,
                resolution.content,
                is_package,
                resolution.hash,
                resolution.storage_path,
            )
            spec = ModuleSpec(
                fullname,
                loader,
                is_package=is_package,
                origin=resolution.path,
            )
            logger.debug(f"Virtual import: {fullname} -> {resolution.path}")
            return spec

        if resolution.kind == "namespace":
            loader = NamespacePackageLoader(resolution.path)
            spec = ModuleSpec(
                fullname,
                loader,
                is_package=True,
                origin=None,
            )
            spec.submodule_search_locations = [resolution.path]
            logger.debug(f"Virtual namespace package: {fullname} -> {resolution.path}/")
            return spec

        return None


def _is_bifrost_source_spec(spec: ModuleSpec) -> bool:
    """Return whether a PathFinder result came from Bifrost application source.

    Installed dependencies, including packages with unusual install layouts,
    retain normal Python precedence. Only modules found inside Bifrost's own
    source roots defer to the targeted workspace resolver so an accidental
    platform-name collision cannot shadow Solution/workspace code.
    """
    locations = [spec.origin or "", *(spec.submodule_search_locations or ())]
    for location in locations:
        if not location or location in {"built-in", "frozen"}:
            continue
        resolved = Path(location).resolve()
        if any(resolved.is_relative_to(root) for root in _BIFROST_SOURCE_ROOTS):
            return True
    return False

# Global finder instance (for invalidation access)
_finder: VirtualModuleFinder | None = None


def install_virtual_import_hook() -> VirtualModuleFinder:
    """
    Install the virtual import hook.

    Must be called in worker before any workspace imports.
    The hook is installed at the front of sys.meta_path so it
    takes precedence over the filesystem finder.

    IMPORTANT: We pre-load encoding modules BEFORE installing the hook.
    This ensures all encoding modules (like encodings.idna for hostname
    resolution) are loaded before our hook can intercept imports.

    Returns:
        The installed finder instance (for testing/invalidation)
    """
    global _finder

    if _finder is not None:
        logger.debug("Virtual import hook already installed")
        return _finder

    # Pre-load encodings that Redis might need for hostname resolution.
    # This must happen BEFORE we install the hook, otherwise the hook
    # might try to fetch from Redis before Redis can even connect.
    _preload_required_modules()

    # BuiltinImporter and FrozenImporter keep their normal precedence. Install
    # immediately before PathFinder so workspace packages can still override an
    # accidental same-named Bifrost source module.
    _finder = VirtualModuleFinder()
    path_finder_index = next(
        (index for index, finder in enumerate(sys.meta_path) if finder is PathFinder),
        len(sys.meta_path),
    )
    sys.meta_path.insert(path_finder_index, _finder)

    logger.info("Virtual import hook installed")
    return _finder


def _preload_required_modules() -> None:
    """
    Pre-load modules that Redis/socket might need.

    This is called BEFORE installing the import hook to ensure
    all encoding and network modules are available without
    triggering our custom finder.
    """
    # Force encodings.idna to be loaded (needed for hostname resolution)
    try:
        import encodings.idna  # noqa: F401
    except ImportError as e:
        # Stdlib module — should always exist; trimmed Python build would log here
        logger.debug(f"encodings.idna preload failed: {e}")

    # Force other encoding modules that might be needed
    try:
        import encodings.utf_8  # noqa: F401
        import encodings.ascii  # noqa: F401
    except ImportError as e:
        # Stdlib modules — log if a stripped build is missing them
        logger.debug(f"encodings.utf_8/ascii preload failed: {e}")

    # Force stringprep (needed by encodings.idna)
    try:
        import stringprep  # noqa: F401
    except ImportError as e:
        # Stdlib module — log if missing
        logger.debug(f"stringprep preload failed: {e}")

    # Ensure codecs is loaded
    try:
        import codecs  # noqa: F401
    except ImportError as e:
        # Stdlib module — should never fail
        logger.debug(f"codecs preload failed: {e}")

    # Build the scope-neutral HTTP client before the finder is active. httpx
    # and httpcore load some optional transports lazily while constructing the
    # client; if that happens after hook installation, a missing optional
    # package (for example trio) can recursively enter the workspace resolver.
    # The client carries no auth or Solution state — those are supplied per
    # request — so it is safe to retain across context activation in this
    # one-shot child.
    try:
        from src.core.module_cache_sync import _get_http_client

        _get_http_client()
    except Exception as e:
        logger.debug(f"module resolver HTTP client preload failed: {e}")


def remove_virtual_import_hook() -> None:
    """
    Remove the virtual import hook.

    Used for testing cleanup.
    """
    global _finder

    if _finder is not None:
        sys.meta_path = [f for f in sys.meta_path if f is not _finder]
        _finder = None
        from src.core.module_cache_sync import _close_http_client

        _close_http_client()
        logger.info("Virtual import hook removed")


def get_virtual_finder() -> VirtualModuleFinder | None:
    """
    Get the current virtual finder instance.

    Returns:
        The active VirtualModuleFinder or None if not installed
    """
    return _finder
