import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy
import run


class AlternativeTest(unittest.TestCase):
    def test_larger_single_worker_preserves_total_resources_and_two_apis(self):
        profile = run.load_profile('hosting-4gb-fixed-workers-1-memory1024-cpu1000m')
        original = run.load_profile('hosting-4gb-fixed-workers-2')
        self.assertEqual(profile['components'], original['components'])
        self.assertEqual(profile['quota'], original['quota'])
        self.assertEqual(profile['components']['api']['replicas'], 2)
        self.assertEqual(profile['worker_policy']['max_workers'] * profile['worker_policy']['worker_memory_mib'],
                         original['worker_policy']['max_workers'] * original['worker_policy']['worker_memory_mib'])
        self.assertEqual(profile['worker_cpu'], '1000m')
        deploy.check_profile(profile)

    def test_elastic_alternatives_change_only_the_floor(self):
        fixed = run.load_profile('hosting-4gb-fixed-workers-1-memory1024-cpu1000m')
        for warm in (0, 1):
            profile = run.load_profile(f'hosting-4gb-elastic-warm{warm}-max1-memory1024-cpu1000m')
            self.assertEqual(profile['components'], fixed['components'])
            self.assertEqual(profile['worker_policy'], {**fixed['worker_policy'], 'warm_workers': warm})
            deploy.check_profile(profile)


if __name__ == '__main__':
    unittest.main()
