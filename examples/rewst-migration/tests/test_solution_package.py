"""Portable manifest tripwires for the migration Solution."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SolutionPackageTests(unittest.TestCase):
    def test_declares_private_storage_configs_and_every_ui_workflow(self) -> None:
        configs = (ROOT / ".bifrost/configs.yaml").read_text()
        files = (ROOT / ".bifrost/files.yaml").read_text()
        workflows = (ROOT / ".bifrost/workflows.yaml").read_text()
        functions = (ROOT / "functions/migration.py").read_text()
        for key in (
            "rewst_migration_api_url",
            "rewst_migration_org_id",
            "rewst_migration_app_session",
        ):
            self.assertIn(key, configs)
        self.assertIn("type: secret", configs)
        self.assertIn("default: null", configs)
        self.assertIn("migration-data", files)
        for function in (
            "migration_get_overview",
            "migration_create",
            "migration_validate_connection",
            "migration_export_environment",
            "migration_get_context",
            "migration_save_plan",
            "migration_update_item",
            "migration_read_artifact",
        ):
            self.assertIn(f"function_name: {function}", workflows)
            self.assertIn(f"async def {function}", functions)
        self.assertEqual(workflows.count("access_level: role_based"), 8)
        self.assertIn("is_platform_admin", functions)


if __name__ == "__main__":
    unittest.main()
