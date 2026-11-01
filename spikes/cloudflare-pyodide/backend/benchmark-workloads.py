"""Fixed workloads shared as a pinned workspace module, using actual upstream SDK."""
import asyncio
import json
import logging
import time

from bifrost import config, integrations, tables


async def measure(key, integration_name, table):
    async def json_transform(index):
        rows = json.loads(json.dumps([{'id': n, 'value': n * 2} for n in range(1000)]))
        return sum(row['value'] for row in rows) == 999000

    async def http_orchestration(index):
        # Two real API requests through the SDK; no mocked transport or sleep.
        return await asyncio.gather(config.get(key), config.get(key)) == [42, 42]

    async def integration_read(index):
        integration = await integrations.get(integration_name)
        return integration.config['qualification_value'] == 42

    async def table_update(index):
        document = await tables.upsert(table, id=f'benchmark-{index}', data={'value': 70})
        return document.data['value'] == 70

    async def multi_step(index):
        first = await config.get(key)
        await asyncio.sleep(0)
        try:
            raise ValueError('fixed handled benchmark exception')
        except ValueError:
            logging.getLogger(__name__).warning('fixed handled benchmark exception')
        second = await config.get(key)
        return first + second == 84

    results = []
    for name, operation in (
        ('json_transform', json_transform), ('http_orchestration', http_orchestration),
        ('integration_read', integration_read), ('table_update', table_update),
        ('multi_step', multi_step),
    ):
        samples = []
        for index in range(3):
            started = time.perf_counter()
            correct = await operation(index)
            elapsed_ms = (time.perf_counter() - started) * 1000
            if correct is not True:
                raise RuntimeError(f'Benchmark correctness failed: {name}')
            samples.append(elapsed_ms)
        results.append({'workload': name, 'samples_wall_ms': samples,
                        'median_wall_ms': sorted(samples)[1], 'correctness': True})
    return {'benchmark': results, 'benchmark_scope': 'three samples within one workflow',
            'benchmark_cpu_ms': None, 'benchmark_peak_isolate_memory_bytes': None,
            'benchmark_cost_per_1000': None}
