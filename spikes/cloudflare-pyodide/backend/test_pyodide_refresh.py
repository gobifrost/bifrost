"""Bridge delegation tests; actual SDK/API qualification is a separate Worker run."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import asyncio
import pyodide_refresh


class PyodideRefreshTests(unittest.TestCase):
    def test_install_replaces_physical_bridges_without_replacing_core(self):
        core = Mock()
        sdk = SimpleNamespace(_refresh_locked=core)
        with patch.dict(sys.modules, {"bifrost": SimpleNamespace(client=sdk)}):
            pyodide_refresh.install()
            pyodide_refresh.install()
        self.assertIs(sdk._refresh_connection_access_token_sync, pyodide_refresh.refresh_sync)
        self.assertIs(sdk.refresh_connection_access_token, pyodide_refresh.refresh_async)
        self.assertIs(sdk._refresh_locked, core)

    def test_delegates_and_registers_returned_token(self):
        pending = object()
        sdk = SimpleNamespace(refresh_connection_access_token=Mock(return_value=pending),
                              _register_refreshed=Mock())
        bridge = Mock(return_value="replacement")
        with patch.dict(sys.modules, {"bifrost": SimpleNamespace(client=sdk),
                                     "pyodide.ffi": SimpleNamespace(run_sync=bridge)}):
            result = pyodide_refresh.refresh_sync("https://test.invalid", "observed")
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
                pyodide_refresh.refresh_sync("https://test.invalid", "observed")
        self.assertEqual(sdk.refresh_connection_access_token.call_count, 1)
        sdk._register_refreshed.assert_not_called()


class CooperativeRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        pyodide_refresh._locks.clear()
        self.assertFalse(pyodide_refresh._pending)

    async def test_same_connection_core_never_runs_concurrently(self):
        active = 0
        maximum = 0
        calls = []

        async def core(url, coordinator, observed):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            calls.append((url, coordinator, observed))
            await asyncio.sleep(0)
            active -= 1
            return "replacement"

        coordinator = object()
        sdk = SimpleNamespace(_refresh_locked=core,
            _refresh_coordinator=Mock(return_value=coordinator), _register_refreshed=Mock())
        with patch.dict(sys.modules, {"bifrost": SimpleNamespace(client=sdk)}):
            values = await asyncio.gather(*(pyodide_refresh.refresh_async("https://test.invalid/", "observed") for _ in range(3)))
        self.assertEqual(values, ["replacement"] * 3)
        self.assertEqual(maximum, 1)
        self.assertEqual(calls, [("https://test.invalid", coordinator, "observed")] * 3)

    async def test_cancelled_waiter_does_not_cancel_core_completion(self):
        started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def core(*_args):
            started.set()
            await release.wait()
            completed.set()
            return "replacement"

        sdk = SimpleNamespace(_refresh_locked=core,
            _refresh_coordinator=Mock(return_value=object()), _register_refreshed=Mock())
        with patch.dict(sys.modules, {"bifrost": SimpleNamespace(client=sdk)}):
            waiter = asyncio.create_task(pyodide_refresh.refresh_async("https://test.invalid", "observed"))
            await started.wait()
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertFalse(completed.is_set())
            self.assertEqual(len(pyodide_refresh._pending), 1)
            release.set()
            await asyncio.gather(*pyodide_refresh._pending)
            self.assertTrue(completed.is_set())
