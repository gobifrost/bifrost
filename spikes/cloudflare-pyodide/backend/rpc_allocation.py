"""Python-tracked serializer allocations, excluding prebuilt workflow inputs."""
import json

from rpc_serialization import encode_engine_rpc


def measure_rpc_allocations():
    try:
        import tracemalloc
    except ImportError as exc:
        return {'available': False, 'error_type': type(exc).__name__, 'module': exc.name}
    if tracemalloc.is_tracing():
        raise RuntimeError('Existing allocation tracer must remain untouched')
    samples = []
    for payload_bytes in (262144, 1048576, 5000000):
        outcome = {'kind': 'engine_result', 'body': {'payload': 'x' * payload_bytes}}
        try:
            tracemalloc.start()
            wire = encode_engine_rpc(outcome, True)
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        samples.append({'payload_bytes': payload_bytes, 'wire_bytes': len(wire),
                        'python_traced_current_bytes': current, 'python_traced_peak_bytes': peak,
                        'delivery_failure': json.loads(wire)['outcome']['kind'] == 'rpc_result_too_large'})
    return {'available': True, 'input_allocation_excluded': True, 'samples': samples}


if __name__ == '__main__':
    print(json.dumps(measure_rpc_allocations()))
