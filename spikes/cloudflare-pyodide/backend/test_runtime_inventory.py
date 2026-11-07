"""Inventory validation only; imports are not execution compatibility proof."""
import copy
from unittest import TestCase, main

from runtime_guard import QUALIFIED_LOCAL_RUNTIME, RuntimeCompatibilityError
from runtime_inventory import validate_inventory, bind_package_requirements


def probe():
    return {'runtime': copy.deepcopy(QUALIFIED_LOCAL_RUNTIME), 'package_environment': 'pyodide',
            'worker_imported': True, 'http_module_path_available': True,
            'packages': [{'name': name, 'version': '1.0.0'} for name in ('httpx', 'pydantic', 'pydantic-core')]}


class InventoryTests(TestCase):
    def test_exact_package_match_normalizes_names_and_snapshots_input(self):
        requirements = {'Pydantic_Core': '1.0.0'}
        bound = bind_package_requirements(validate_inventory(probe()), requirements)
        requirements['Pydantic_Core'] = 'changed'
        self.assertEqual(bound, {'pydantic-core': '1.0.0'})

    def test_missing_mismatched_ambiguous_or_unobserved_packages_refused(self):
        for requirements in [{'httpx': '2.0.0'}, {'missing': '1'},
                             {'pydantic_core': '1.0.0', 'pydantic-core': '1.0.0'},
                             {'httpx': None}, {'httpx': '>=1'}, {'bad name': '1'},
                             {'httpx': '1\nsecret'}, [], None]:
            with self.subTest(requirements=requirements), self.assertRaises(ValueError):
                bind_package_requirements(validate_inventory(probe()), requirements)
        with self.assertRaises(ValueError):
            bind_package_requirements(None, {'httpx': '1.0.0'})
        self.assertEqual(bind_package_requirements(None, {}), {})

    def test_valid_inventory_is_snapshotted(self):
        original = probe()
        frozen = validate_inventory(original)
        original['runtime']['python'][0] = 0
        original['packages'][0]['version'] = 'changed'
        self.assertEqual(frozen, probe())

    def test_wrong_runtime_is_not_a_package_adapter(self):
        original = probe()
        original['runtime']['soabi'] = 'cpython-314-x86_64-linux-gnu'
        with self.assertRaises(RuntimeCompatibilityError):
            validate_inventory(original)

    def test_missing_core_package_and_unobserved_version_rejected(self):
        for packages in [[], probe()['packages'][:-1], [{'name': 'httpx', 'version': None}]]:
            with self.subTest(packages=packages), self.assertRaises(ValueError):
                validate_inventory({**probe(), 'packages': packages})

    def test_ambiguous_identity_and_command_like_name_rejected(self):
        for extra in [{'name': 'pydantic_core', 'version': '1.0.0'}, {'name': '--install', 'version': '1'}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_inventory({**probe(), 'packages': probe()['packages'] + [extra]})

    def test_linux_environment_or_missing_engine_probe_rejected(self):
        for change in [{'package_environment': 'linux'}, {'worker_imported': False}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_inventory({**probe(), **change})


if __name__ == '__main__':
    main()
