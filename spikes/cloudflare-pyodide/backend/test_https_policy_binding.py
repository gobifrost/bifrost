"""Trusted configuration validation; no network or authorization mocks."""
from dataclasses import FrozenInstanceError
from unittest import TestCase
from uuid import uuid4

from https_policy_binding import snapshot_policies


class HttpsBindingTests(TestCase):
    def test_default_is_empty_and_snapshot_is_immutable(self):
        self.assertEqual(snapshot_policies(None), {})
        config = {'vendor': {'org_id': str(uuid4()), 'origins': ['https://vendor.example'], 'max_requests': 2}}
        bound = snapshot_policies(config)['vendor']
        config['vendor']['origins'].clear()
        self.assertEqual(bound.origins, ('https://vendor.example',))
        with self.assertRaises(FrozenInstanceError):
            setattr(bound, 'max_requests', 99)

    def test_ambiguous_or_unbounded_operator_config_is_refused(self):
        for origin in ['http://vendor.example', 'https://vendor.example/', 'https://localhost',
                       'https://127.0.0.1', 'https://[::1]', 'https://user@vendor.example',
                       'https://vendor.example?x', 'https://vendor.example:443',
                       'https://vendor.example#x', 'https://VENDOR.example', 'https://*.example']:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                snapshot_policies({'vendor': {'org_id': str(uuid4()), 'origins': [origin], 'max_requests': 2}})
        for limit in [0, 101, True, 1.5]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                snapshot_policies({'vendor': {'org_id': str(uuid4()), 'origins': ['https://vendor.example'], 'max_requests': limit}})
