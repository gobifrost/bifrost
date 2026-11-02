import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db_profile


class CounterTests(unittest.TestCase):
    def test_refuses_hook_command_injection(self):
        cluster = Mock()
        with self.assertRaises(ValueError):
            db_profile.command(cluster, 'pod', "bad-label';import os", 'start')
        cluster.kubectl.assert_not_called()

    def test_stop_retains_json(self):
        cluster = Mock()
        cluster.kubectl.return_value = json.dumps({'sql': [], 'pool_events': []})
        self.assertEqual(db_profile.command(cluster, 'api-1', 'trial-1', 'stop'),
                         {'sql': [], 'pool_events': []})
        self.assertEqual(cluster.kubectl.call_args.args[:5], ('exec', 'api-1', '-c', 'api', '--'))

    def test_retains_both_replica_lifetimes(self):
        cluster = Mock()
        cluster.api_pod_records.return_value = [
            {'pod': 'api-1', 'pod_uid': 'uid1'}, {'pod': 'api-2', 'pod_uid': 'uid2'}]
        done = threading.Event()
        def counter(_cluster, pod, _label, action):
            if pod == 'api-2' and action == 'stop':
                done.set()
            return {'sql': []} if action == 'stop' else None
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'counters.json'
            with patch.object(db_profile, 'command', side_effect=counter):
                with db_profile.measure(cluster, 'trial-1', out, delay_s=0, duration_s=.001):
                    self.assertTrue(done.wait(1))
            value = json.loads(out.read_text())
            self.assertEqual(value['status'], 'pass')
            self.assertEqual([r['pod_uid'] for r in value['replicas']], ['uid1', 'uid2'])

    def test_counter_failure_is_durable_and_propagated(self):
        cluster = Mock()
        cluster.api_pod_records.return_value = [{'pod': 'api-1', 'pod_uid': 'uid1'}]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'counters.json'
            with patch.object(db_profile, 'command', side_effect=RuntimeError('probe failed')):
                with self.assertRaises(RuntimeError):
                    with db_profile.measure(cluster, 'trial-1', out, delay_s=0, duration_s=.001):
                        import time
                        time.sleep(.01)
            value = json.loads(out.read_text())
            self.assertEqual(value['status'], 'invalid')
            self.assertEqual(value['api_pods'][0]['pod_uid'], 'uid1')


if __name__ == '__main__':
    unittest.main()
