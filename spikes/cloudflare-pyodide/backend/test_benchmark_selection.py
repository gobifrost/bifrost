"""Benchmark fixture guards only; successful SDK compatibility needs live evidence."""
import importlib.util
from pathlib import Path
import runpy
import unittest
from unittest.mock import AsyncMock, patch


def workloads():
    spec = importlib.util.spec_from_file_location('benchmark_workloads', Path(__file__).with_name('benchmark-workloads.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BenchmarkSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_workload_rejected_before_sdk_effects(self):
        module = workloads()
        read = AsyncMock(side_effect=AssertionError('SDK must not be called'))
        with patch.object(module.config, 'get', read):
            with self.assertRaisesRegex(ValueError, 'Unsupported fixed benchmark'):
                await module.measure('key', 'integration', 'table', selected='unknown')
        read.assert_not_awaited()

    async def test_selected_json_workload_is_real_pure_python_and_only_three_samples(self):
        module = workloads()
        read = AsyncMock(side_effect=AssertionError('No SDK calls in JSON transform'))
        with patch.object(module.config, 'get', read):
            result = await module.measure('key', 'integration', 'table', selected='json_transform')
        self.assertEqual(len(result['benchmark']), 1)
        row = result['benchmark'][0]
        self.assertEqual(row['workload'], 'json_transform')
        self.assertTrue(row['correctness'])
        self.assertEqual(len(row['samples_wall_ms']), 3)
        self.assertIsNone(result['benchmark_cpu_ms'])
        self.assertIsNone(result['benchmark_peak_isolate_memory_bytes'])
        self.assertIsNone(result['benchmark_cost_per_1000'])
        read.assert_not_awaited()

    async def test_invalid_control_fixture_selection_rejected_before_database_import(self):
        main = runpy.run_path(str(Path(__file__).with_name('authorized-local.py')))['main']
        with self.assertRaisesRegex(ValueError, 'Unsupported fixed benchmark'):
            await main(benchmark='unknown')


if __name__ == '__main__':
    unittest.main()
