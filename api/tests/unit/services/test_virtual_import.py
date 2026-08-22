"""
Unit tests for the virtual import hook.

Tests the MetaPathFinder implementation that loads modules from Redis cache.

The implementation uses a targeted resolver to identify workspace-owned imports:
- Third-party imports fall through to normal import handling first
- Workspace imports load source returned by the resolver
- Thread-local recursion guard prevents infinite loops during Redis/API/S3 calls
"""

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from src.core.module_cache_sync import ModuleResolution
from src.services.execution.virtual_import import (
    _is_bifrost_source_spec,
    NamespacePackageLoader,
    VirtualModuleFinder,
    VirtualModuleLoader,
    get_virtual_finder,
    install_virtual_import_hook,
    remove_virtual_import_hook,
)


class TestVirtualModuleFinder:
    """Tests for VirtualModuleFinder class."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Clean up after each test."""
        yield
        # Remove any virtual import hooks from sys.meta_path
        sys.meta_path = [
            finder
            for finder in sys.meta_path
            if not finder.__class__.__name__ == "VirtualModuleFinder"
        ]
        # Reset global finder
        import src.services.execution.virtual_import as module

        module._finder = None

    def test_find_spec_module_not_in_cache(self):
        """Test find_spec returns None when module is not in cache."""
        finder = VirtualModuleFinder()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(kind="not_found", path="nonexistent/module"),
        ) as mock_resolve:
            spec = finder.find_spec("nonexistent.module")
            assert spec is None
        mock_resolve.assert_called_once_with("nonexistent.module")

    def test_find_spec_module_in_cache(self):
        """Test find_spec returns spec when module is in cache."""
        finder = VirtualModuleFinder()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(
                kind="module",
                path="shared/halopsa.py",
                storage_path="_solutions/solution-a/shared/halopsa.py",
                content="x = 1",
                hash="abc",
            ),
        ):
            spec = finder.find_spec("shared.halopsa")

            assert spec is not None
            assert spec.name == "shared.halopsa"
            assert spec.loader is not None
            assert spec.origin == "shared/halopsa.py"
            assert not spec.submodule_search_locations  # Not a package
            assert isinstance(spec.loader, VirtualModuleLoader)
            assert spec.loader.storage_path == "_solutions/solution-a/shared/halopsa.py"

    def test_find_spec_package_in_cache(self):
        """Test find_spec returns package spec for __init__.py."""
        finder = VirtualModuleFinder()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(
                kind="package",
                path="shared/__init__.py",
                content="# package",
                hash="abc",
            ),
        ), patch("src.services.execution.virtual_import.PathFinder.find_spec", return_value=None):
            spec = finder.find_spec("shared")

            assert spec is not None
            assert spec.name == "shared"
            assert spec.submodule_search_locations is not None  # Is a package

    def test_find_spec_prefers_module_over_package(self):
        """Test that .py file is tried before __init__.py."""
        finder = VirtualModuleFinder()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(
                kind="module",
                path="shared.py",
                content="x = 1",
                hash="abc",
            ),
        ), patch("src.services.execution.virtual_import.PathFinder.find_spec", return_value=None):
            spec = finder.find_spec("shared")

            assert spec is not None
            assert spec.origin == "shared.py"  # Module file, not package

    def test_find_spec_namespace_package(self):
        """Test find_spec returns namespace package when submodules exist."""
        finder = VirtualModuleFinder()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(kind="namespace", path="modules"),
        ):
            # "modules" should become a namespace package
            spec = finder.find_spec("modules")

            assert spec is not None
            assert spec.name == "modules"
            assert spec.origin is None  # Namespace packages have no origin
            assert spec.submodule_search_locations == ["modules"]
            assert isinstance(spec.loader, NamespacePackageLoader)

    def test_find_spec_nested_namespace_package(self):
        """Test find_spec returns namespace package for nested directories."""
        finder = VirtualModuleFinder()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(kind="namespace", path="modules/extensions"),
        ):
            # "modules.extensions" should also be a namespace package
            spec = finder.find_spec("modules.extensions")

            assert spec is not None
            assert spec.name == "modules.extensions"
            assert spec.origin is None
            assert spec.submodule_search_locations == ["modules/extensions"]

    def test_find_spec_no_namespace_without_submodules(self):
        """Test find_spec returns None when no submodules exist."""
        finder = VirtualModuleFinder()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(kind="not_found", path="nonexistent"),
        ):
            spec = finder.find_spec("nonexistent")

            assert spec is None  # Not a namespace package

    def test_find_spec_prefers_explicit_init_over_namespace(self):
        """Test that __init__.py takes precedence over namespace package."""
        finder = VirtualModuleFinder()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(
                kind="package",
                path="modules/__init__.py",
                content="# explicit package",
                hash="abc",
            ),
        ), patch("src.services.execution.virtual_import.PathFinder.find_spec", return_value=None):
            spec = finder.find_spec("modules")

            assert spec is not None
            # Should be the explicit __init__.py, not namespace package
            assert spec.origin == "modules/__init__.py"
            assert isinstance(spec.loader, VirtualModuleLoader)

    def test_find_spec_skips_stdlib_modules(self):
        """Test that stdlib modules are not looked up in cache."""
        finder = VirtualModuleFinder()

        mock_resolve = MagicMock()
        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            mock_resolve,
        ):
            assert finder.find_spec("os") is None
            assert finder.find_spec("sys") is None
            assert finder.find_spec("json") is None
            assert finder.find_spec("redis") is None

            mock_resolve.assert_not_called()

    def test_find_spec_skips_non_workspace_third_party_modules(self):
        """Third-party dependencies not in the module index should not hit cache lookup."""
        finder = VirtualModuleFinder()
        mock_resolve = MagicMock(return_value=ModuleResolution(kind="not_found", path="httpcore"))

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            mock_resolve,
        ):
            assert finder.find_spec("httpcore") is None

        mock_resolve.assert_not_called()

    def test_find_spec_recursion_guard(self):
        """Test that recursion guard prevents infinite loops."""
        from src.services.execution.virtual_import import _thread_local

        finder = VirtualModuleFinder()

        # Simulate being in a recursive call
        _thread_local.in_find_spec = True
        try:
            # Should return None immediately without doing anything
            spec = finder.find_spec("some.module")
            assert spec is None
        finally:
            _thread_local.in_find_spec = False

    def test_identifies_bifrost_source_specs(self):
        """Only Bifrost application source defers to workspace resolution."""
        from importlib.machinery import ModuleSpec
        from src.services.execution import virtual_import

        source_path = virtual_import._BIFROST_SOURCE_ROOTS[0] / "services" / "collision.py"
        source_spec = ModuleSpec("collision", loader=None, origin=str(source_path))
        installed_spec = ModuleSpec(
            "dependency",
            loader=None,
            origin="/usr/local/lib/python3.14/site-packages/dependency.py",
        )

        assert _is_bifrost_source_spec(source_spec)
        assert not _is_bifrost_source_spec(installed_spec)

class TestNamespacePackageLoader:
    """Tests for NamespacePackageLoader class."""

    def test_create_module_returns_none(self):
        """Test create_module returns None for default semantics."""
        from importlib.machinery import ModuleSpec

        loader = NamespacePackageLoader("modules")
        spec = ModuleSpec("modules", loader, is_package=True)

        result = loader.create_module(spec)

        assert result is None

    def test_exec_module_sets_path(self):
        """Test exec_module sets __path__ for submodule resolution."""
        loader = NamespacePackageLoader("modules/extensions")
        module = ModuleType("modules.extensions")

        loader.exec_module(module)

        assert hasattr(module, "__path__")
        assert module.__path__ == ["modules/extensions"]

    def test_exec_module_sets_no_file(self):
        """Test exec_module sets __file__ to None (namespace packages have no file)."""
        loader = NamespacePackageLoader("modules")
        module = ModuleType("modules")

        loader.exec_module(module)

        assert module.__file__ is None

    def test_exec_module_sets_loader(self):
        """Test exec_module sets __loader__ to self."""
        loader = NamespacePackageLoader("modules")
        module = ModuleType("modules")

        loader.exec_module(module)

        assert module.__loader__ is loader


class TestVirtualModuleLoader:
    """Tests for VirtualModuleLoader class."""

    def test_create_module_returns_none(self):
        """Test create_module returns None for default semantics."""
        from importlib.machinery import ModuleSpec

        loader = VirtualModuleLoader("test.py", "x = 1", is_package=False)
        spec = ModuleSpec("test", loader)

        result = loader.create_module(spec)

        assert result is None

    def test_exec_module_sets_file_attribute(self):
        """Test exec_module sets __file__ to virtual path."""
        loader = VirtualModuleLoader(
            "shared/test.py",
            "x = 1",
            is_package=False,
            storage_path="_solutions/solution-a/shared/test.py",
        )
        module = ModuleType("shared.test")

        loader.exec_module(module)

        assert module.__file__ == "shared/test.py"
        assert module.__loader__ is loader
        assert module.__storage_path__ == "_solutions/solution-a/shared/test.py"

    def test_exec_module_sets_path_for_package(self):
        """Test exec_module sets __path__ for packages."""
        loader = VirtualModuleLoader("shared/__init__.py", "# package", is_package=True)
        module = ModuleType("shared")

        loader.exec_module(module)

        assert hasattr(module, "__path__")
        assert module.__path__ == ["shared"]

    def test_exec_module_executes_code(self):
        """Test exec_module executes the Python code."""
        loader = VirtualModuleLoader("test.py", "x = 42\ndef hello(): return 'world'")
        module = ModuleType("test")

        loader.exec_module(module)

        assert module.x == 42
        assert module.hello() == "world"

    def test_exec_module_raises_on_syntax_error(self):
        """Test exec_module raises SyntaxError for invalid code."""
        loader = VirtualModuleLoader("test.py", "def broken(")
        module = ModuleType("test")

        with pytest.raises(SyntaxError):
            loader.exec_module(module)

    def test_exec_module_raises_on_runtime_error(self):
        """Test exec_module propagates runtime errors."""
        loader = VirtualModuleLoader("test.py", "raise ValueError('test error')")
        module = ModuleType("test")

        with pytest.raises(ValueError, match="test error"):
            loader.exec_module(module)


class TestInstallRemoveHook:
    """Tests for hook installation and removal functions."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Clean up after each test."""
        yield
        # Remove any virtual import hooks
        sys.meta_path = [
            finder
            for finder in sys.meta_path
            if not finder.__class__.__name__ == "VirtualModuleFinder"
        ]
        # Reset global finder
        import src.services.execution.virtual_import as module

        module._finder = None

    def test_install_virtual_import_hook(self):
        """Test installing the virtual import hook."""
        initial_count = len(sys.meta_path)

        finder = install_virtual_import_hook()

        assert isinstance(finder, VirtualModuleFinder)
        assert len(sys.meta_path) == initial_count + 1
        finder_index = sys.meta_path.index(finder)
        path_finder_index = sys.meta_path.index(importlib.machinery.PathFinder)
        assert finder_index == path_finder_index - 1
        assert importlib.machinery.BuiltinImporter in sys.meta_path[:finder_index]
        assert importlib.machinery.FrozenImporter in sys.meta_path[:finder_index]

    def test_http_client_is_built_before_hook_becomes_visible(self):
        """Lazy HTTP transport imports must not recurse into the resolver."""
        def build_client():
            assert not any(
                finder.__class__.__name__ == "VirtualModuleFinder"
                for finder in sys.meta_path
            )
            return MagicMock()

        with patch(
            "src.core.module_cache_sync._get_http_client",
            side_effect=build_client,
        ) as get_client:
            install_virtual_import_hook()

        get_client.assert_called_once_with()

    def test_install_virtual_import_hook_idempotent(self):
        """Test that installing twice returns same finder."""
        finder1 = install_virtual_import_hook()
        initial_count = len(sys.meta_path)

        finder2 = install_virtual_import_hook()

        assert finder1 is finder2
        assert len(sys.meta_path) == initial_count  # Not added again

    def test_remove_virtual_import_hook(self):
        """Test removing the virtual import hook."""
        install_virtual_import_hook()
        initial_count = len(sys.meta_path)

        remove_virtual_import_hook()

        assert len(sys.meta_path) == initial_count - 1
        assert not any(
            finder.__class__.__name__ == "VirtualModuleFinder" for finder in sys.meta_path
        )

    def test_remove_virtual_import_hook_noop_when_not_installed(self):
        """Test removing when hook is not installed."""
        initial_count = len(sys.meta_path)

        # Should not raise
        remove_virtual_import_hook()

        assert len(sys.meta_path) == initial_count


class TestGetVirtualFinder:
    """Tests for get_virtual_finder function."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Clean up after each test."""
        yield
        sys.meta_path = [
            finder
            for finder in sys.meta_path
            if not finder.__class__.__name__ == "VirtualModuleFinder"
        ]
        import src.services.execution.virtual_import as module

        module._finder = None

    def test_get_virtual_finder_when_installed(self):
        """Test getting finder when installed."""
        installed = install_virtual_import_hook()
        result = get_virtual_finder()

        assert result is installed

    def test_get_virtual_finder_when_not_installed(self):
        """Test getting finder when not installed."""
        result = get_virtual_finder()

        assert result is None


class TestIntegration:
    """Integration tests for the virtual import system."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Clean up after each test."""
        yield
        # Remove virtual import hooks
        sys.meta_path = [
            finder
            for finder in sys.meta_path
            if not finder.__class__.__name__ == "VirtualModuleFinder"
        ]
        # Remove test modules from sys.modules
        to_remove = [k for k in sys.modules if k.startswith("virtual_test_")]
        for k in to_remove:
            del sys.modules[k]
        # Reset global finder
        import src.services.execution.virtual_import as module

        module._finder = None

    def test_import_module_from_cache(self):
        """Test actually importing a module from cache."""
        install_virtual_import_hook()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            return_value=ModuleResolution(
                kind="module",
                path="virtual_test_module.py",
                content="MAGIC_VALUE = 12345\ndef get_magic(): return MAGIC_VALUE",
                hash="abc",
            ),
        ):
            import virtual_test_module  # type: ignore[import-not-found]

            assert virtual_test_module.MAGIC_VALUE == 12345
            assert virtual_test_module.get_magic() == 12345
            assert virtual_test_module.__file__ == "virtual_test_module.py"

    def test_import_nested_module_from_cache(self):
        """Test importing a nested module from cache."""
        install_virtual_import_hook()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            side_effect=[
                ModuleResolution(
                    kind="package",
                    path="virtual_test_pkg/__init__.py",
                    content="PKG_VALUE = 'package'",
                    hash="abc",
                ),
                ModuleResolution(
                    kind="module",
                    path="virtual_test_pkg/submod.py",
                    content="SUB_VALUE = 'submodule'",
                    hash="def",
                ),
            ],
        ):
            import virtual_test_pkg  # type: ignore[import-not-found]
            from virtual_test_pkg import submod  # type: ignore[import-not-found]

            assert virtual_test_pkg.PKG_VALUE == "package"
            assert submod.SUB_VALUE == "submodule"

    def test_import_falls_back_to_filesystem(self):
        """Test that import falls back to filesystem when not in cache."""
        install_virtual_import_hook()

        with patch("src.services.execution.virtual_import.resolve_module_sync") as mock_resolve:
            # Should fall back to normal import
            import json

            assert json is not None
            assert hasattr(json, "loads")
            mock_resolve.assert_not_called()

    def test_import_from_namespace_package(self):
        """Test importing from a directory without __init__.py (namespace package)."""
        install_virtual_import_hook()

        with patch(
            "src.services.execution.virtual_import.resolve_module_sync",
            side_effect=[
                ModuleResolution(kind="namespace", path="virtual_test_ns"),
                ModuleResolution(kind="namespace", path="virtual_test_ns/extensions"),
                ModuleResolution(
                    kind="module",
                    path="virtual_test_ns/extensions/helper.py",
                    content="HELPER_VALUE = 'from namespace'",
                    hash="abc",
                ),
            ],
        ):
            # Import the nested module - parent packages become namespace packages
            from virtual_test_ns.extensions import helper  # type: ignore[import-not-found]

            assert helper.HELPER_VALUE == "from namespace"
            assert helper.__file__ == "virtual_test_ns/extensions/helper.py"

            # Parent namespace packages should have no __file__
            import virtual_test_ns  # type: ignore[import-not-found]
            import virtual_test_ns.extensions  # type: ignore[import-not-found]

            assert virtual_test_ns.__file__ is None
            assert virtual_test_ns.extensions.__file__ is None
            assert hasattr(virtual_test_ns, "__path__")
            assert hasattr(virtual_test_ns.extensions, "__path__")

    def test_installed_dependency_takes_precedence_over_virtual_package(self, tmp_path):
        """Installed requirements should be resolved before workspace modules."""
        module_name = "virtual_collision_pkg"
        installed_dir = tmp_path / "site-packages"
        installed_dir.mkdir()
        filesystem_module = installed_dir / f"{module_name}.py"
        filesystem_module.write_text("ORIGIN = 'filesystem'\n")
        sys.path.insert(0, str(installed_dir))
        install_virtual_import_hook()

        try:
            with patch("src.services.execution.virtual_import.resolve_module_sync") as mock_resolve:
                module = importlib.import_module(module_name)

                assert module.ORIGIN == "filesystem"
                mock_resolve.assert_not_called()
        finally:
            sys.path.remove(str(installed_dir))
            for loaded in [
                module_name,
                f"{module_name}.submodule",
            ]:
                sys.modules.pop(loaded, None)

    def test_virtual_package_takes_precedence_over_platform_source_module(self, tmp_path):
        """Workspace packages should not be shadowed by Bifrost source modules."""
        module_name = "virtual_platform_collision_pkg"
        filesystem_module = tmp_path / f"{module_name}.py"
        filesystem_module.write_text("ORIGIN = 'platform_source'\n")
        sys.path.insert(0, str(tmp_path))
        install_virtual_import_hook()

        resolutions = {
            module_name: ModuleResolution(
                kind="package",
                path=f"{module_name}/__init__.py",
                content="ORIGIN = 'virtual_package'",
                hash="abc",
            ),
            f"{module_name}.submodule": ModuleResolution(
                kind="module",
                path=f"{module_name}/submodule.py",
                content="VALUE = 'from_submodule'",
                hash="def",
            ),
        }

        try:
            with (
                patch(
                    "src.services.execution.virtual_import._is_bifrost_source_spec",
                    return_value=True,
                ),
                patch(
                    "src.services.execution.virtual_import.resolve_module_sync",
                    side_effect=lambda name: resolutions[name],
                ),
            ):
                package = importlib.import_module(module_name)
                submodule = importlib.import_module(f"{module_name}.submodule")

                assert package.ORIGIN == "virtual_package"
                assert submodule.VALUE == "from_submodule"
                assert package.__file__ == f"{module_name}/__init__.py"
        finally:
            sys.path.remove(str(tmp_path))
            for loaded in [module_name, f"{module_name}.submodule"]:
                sys.modules.pop(loaded, None)
