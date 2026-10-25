"""Tests for pure local Kind worker-pool drill invariants."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


KUBERNETES_SCRIPTS = Path(__file__).resolve().parent / "kubernetes"


def pool_drills():
    sys.path.insert(0, str(KUBERNETES_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "pool_drills_under_test", KUBERNETES_SCRIPTS / "pool_drills.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class PoolDrillTests(unittest.TestCase):
    def test_controller_restart_burst_requires_at_least_three_warm_worker_budgets(self) -> None:
        drills = pool_drills()
        warm_budget = 8 * drills.INLINE_RUN_RESERVATION_BYTES

        burst = drills.controller_restart_burst_count(warm_budget)

        self.assertEqual(burst, 17)
        self.assertGreater(burst, warm_budget // drills.INLINE_RUN_RESERVATION_BYTES)

    def test_active_job_binding_accepts_create_window_but_rejects_uid_change(self) -> None:
        drills = pool_drills()
        with (
            patch.object(drills, "worker_job_bindings", return_value={"worker-a": None}),
            patch.object(drills, "active_pool_jobs", return_value={"worker-a": "job-a"}),
        ):
            self.assertEqual(drills.assert_active_jobs_bound(), {"worker-a": "job-a"})

        with (
            patch.object(drills, "worker_job_bindings", return_value={"worker-a": "job-b"}),
            patch.object(drills, "active_pool_jobs", return_value={"worker-a": "job-a"}),
            self.assertRaisesRegex(AssertionError, "changed Job UIDs"),
        ):
            drills.assert_active_jobs_bound()
