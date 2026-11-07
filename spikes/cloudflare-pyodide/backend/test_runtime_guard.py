"""Pure companion guard tests; real Pyodide operation is qualified separately."""
import copy
import unittest
from runtime_guard import check_runtime, QUALIFIED_LOCAL_RUNTIME, RuntimeCompatibilityError


class RuntimeGuardTests(unittest.TestCase):
    def test_exact_observation_is_accepted(self):
        observed = copy.deepcopy(QUALIFIED_LOCAL_RUNTIME)
        self.assertIs(check_runtime(observed), observed)

    def test_drift_cannot_reach_effect_callback(self):
        for field in QUALIFIED_LOCAL_RUNTIME:
            with self.subTest(field=field):
                observed = copy.deepcopy(QUALIFIED_LOCAL_RUNTIME)
                observed[field] = "different"
                effects = []
                with self.assertRaises(RuntimeCompatibilityError):
                    check_runtime(observed)
                    effects.append("upstream import or SDK request")
                self.assertEqual(effects, [])

    def test_missing_or_extra_identity_is_not_accepted(self):
        for observed in ({}, None, {**QUALIFIED_LOCAL_RUNTIME, "extra": True}):
            with self.subTest(observed=observed):
                with self.assertRaises(RuntimeCompatibilityError):
                    check_runtime(observed)
