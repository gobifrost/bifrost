import json
from unittest import TestCase

from rpc_serialization import encode_rpc, encode_engine_rpc, RpcResultTooLargeError, MAX_RPC_BYTES


class RpcSerializationTests(TestCase):
    def test_completed_engine_oversize_has_payload_free_delivery_failure(self):
        wire = encode_engine_rpc({'kind': 'engine_result', 'body': 'sensitive' * 1000}, True, max_bytes=256)
        self.assertEqual(json.loads(wire), {'outcome': {'kind': 'rpc_result_too_large'}, 'sdk_close_returned': True})
        self.assertNotIn('sensitive', wire)

    def test_incomplete_cleanup_and_coroutine_receipts_cannot_be_mapped(self):
        for outcome, closed in [({'kind': 'engine_result', 'body': 'x' * 1000}, False),
                                ({'cooperative_task_cancelled': True, 'body': 'x' * 1000}, True)]:
            with self.assertRaises(RpcResultTooLargeError):
                encode_engine_rpc(outcome, closed, max_bytes=256)

    def test_default_four_mib_boundary(self):
        overhead = len(encode_rpc({'payload': ''}))
        self.assertEqual(len(encode_rpc({'payload': 'x' * (MAX_RPC_BYTES-overhead)})), MAX_RPC_BYTES)
        with self.assertRaises(RpcResultTooLargeError):
            encode_rpc({'payload': 'x' * (MAX_RPC_BYTES-overhead+1)})

    def test_exact_budget_preserves_large_integer(self):
        value = {'n': 9007199254740993}
        wire = '{"n":9007199254740993}'
        self.assertEqual(encode_rpc(value, max_bytes=len(wire)), wire)
        self.assertEqual(json.loads(wire), value)

    def test_one_byte_over_budget_is_refused_without_secret_in_error(self):
        value = {'secret': 'synthetic-sensitive-value'}
        with self.assertRaises(RpcResultTooLargeError) as error:
            encode_rpc(value, max_bytes=2)
        self.assertNotIn(value['secret'], str(error.exception))

    def test_ascii_escape_expansion_counts_toward_budget(self):
        wire = encode_rpc({'value': '\u2603'})
        self.assertTrue(wire.isascii())
        self.assertEqual(json.loads(wire), {'value': '\u2603'})
        with self.assertRaises(RpcResultTooLargeError):
            encode_rpc({'value': '\u2603'}, max_bytes=len(wire)-1)

    def test_nonfinite_and_circular_values_are_not_silently_changed(self):
        for value in [float('nan'), float('inf'), -float('inf')]:
            with self.assertRaises(ValueError):
                encode_rpc(value)
        value = []
        value.append(value)
        with self.assertRaises(ValueError):
            encode_rpc(value)

    def test_invalid_budgets_are_rejected(self):
        for budget in [True, 0, -1, 1.5]:
            with self.assertRaises(ValueError):
                encode_rpc({}, max_bytes=budget)
