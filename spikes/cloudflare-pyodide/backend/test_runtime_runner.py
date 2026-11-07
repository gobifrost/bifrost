"""Bootstrap delegation only; real Pyodide context evidence is separate."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


def load_bootstrap():
    execute = AsyncMock(return_value={'status': 'Success'})
    guard, marker, refresh = Mock(), Mock(), Mock()
    modules = {
        'runtime_guard': SimpleNamespace(require_runtime=guard),
        'src.core.module_cache_sync': SimpleNamespace(mark_execution_child=marker),
        'pyodide_refresh': SimpleNamespace(install=refresh),
        'src.services.execution.worker': SimpleNamespace(run_execution=execute, ResourceMetrics=lambda *values: values),
    }
    spec = importlib.util.spec_from_file_location('runtime_runner_unit', Path(__file__).with_name('runtime-runner.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module, execute, guard, marker, refresh


class RuntimeRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_metadata_does_not_replace_transport_or_context(self):
        module, execute, guard, marker, refresh = load_bootstrap()
        context = {'engine_token': 'synthetic-unused-token', 'parameters': {'n': 7}}
        with patch.dict(os.environ, {}, clear=True):
            result = await module.run_authorized_attempt('run', context, 'https://transport.invalid',
                                                         public_url='https://platform.invalid')
            self.assertEqual(os.environ['BIFROST_API_URL'], 'https://transport.invalid')
            self.assertNotIn('BIFROST_SECRET_KEY', os.environ)
        self.assertEqual(result, {'status': 'Success'})
        self.assertEqual(execute.await_args.args, ('run', context))
        self.assertIs(execute.await_args.args[1], context)
        self.assertEqual(execute.await_args.kwargs['public_url'], 'https://platform.invalid')
        self.assertEqual(execute.await_args.kwargs['resource_observer'].finish(), (None,) * 5)
        guard.assert_called_once_with()
        marker.assert_called_once_with()
        refresh.assert_called_once_with()

    async def test_existing_three_argument_call_preserves_its_public_url(self):
        module, execute, *_ = load_bootstrap()
        with patch.dict(os.environ, {}, clear=True):
            result = await module.run_authorized_attempt('run', {}, 'https://transport.invalid')
        self.assertEqual(result, {'status': 'Success'})
        self.assertEqual(execute.await_args.kwargs['public_url'], 'https://transport.invalid')


if __name__ == '__main__':
    unittest.main()
