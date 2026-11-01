"""Bridge delegation tests; actual SDK/API qualification is a separate Worker run."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pyodide_refresh import install, refresh_sync


class PyodideRefreshTests(unittest.TestCase):
    def test_install_only_replaces_sync_physical_bridge(self):
        sdk = SimpleNamespace(refresh_connection_access_token=Mock())
        original_async = sdk.refresh_connection_access_token
        with patch.dict(sys.modules, {"bifrost": SimpleNamespace(client=sdk)}):
            install()
            install()
        self.assertIs(sdk._refresh_connection_access_token_sync, refresh_sync)
        self.assertIs(sdk.refresh_connection_access_token, original_async)

    def test_delegates_and_registers_returned_token(self):
        pending = object()
        sdk = SimpleNamespace(refresh_connection_access_token=Mock(return_value=pending),
                              _register_refreshed=Mock())
        bridge = Mock(return_value="replacement")
        with patch.dict(sys.modules, {"bifrost": SimpleNamespace(client=sdk),
                                     "pyodide.ffi": SimpleNamespace(run_sync=bridge)}):
            result = refresh_sync("https://test.invalid", "observed")
        self.assertEqual(result, "replacement")
        sdk.refresh_connection_access_token.assert_called_once_with("https://test.invalid", "observed")
        bridge.assert_called_once_with(pending)
        sdk._register_refreshed.assert_called_once_with("replacement")

    def test_native_bridge_failure_propagates_without_an_alternate_refresh(self):
        sdk = SimpleNamespace(refresh_connection_access_token=Mock(return_value=object()),
                              _register_refreshed=Mock())
        bridge = Mock(side_effect=RuntimeError("native bridge failure"))
        with patch.dict(sys.modules, {"bifrost": SimpleNamespace(client=sdk),
                                     "pyodide.ffi": SimpleNamespace(run_sync=bridge)}):
            with self.assertRaisesRegex(RuntimeError, "native bridge failure"):
                refresh_sync("https://test.invalid", "observed")
        self.assertEqual(sdk.refresh_connection_access_token.call_count, 1)
        sdk._register_refreshed.assert_not_called()
