"""Offline contract tests for the Rewst Migration Solution backend."""

from __future__ import annotations

import asyncio
import json
import shlex
import unittest

from modules.rewst_migration.core import (
    AuthorizationError,
    MigrationService,
    SchemaCompatibilityError,
)
from modules.rewst_migration.storage import InMemoryMigrationStorage


class FakeGraphQL:
    """A captured-query substitute; it never contacts a service."""

    def __init__(self, *, failure: Exception | None = None):
        self.failure = failure
        self.calls: list[tuple[str, dict]] = []

    def query(self, query: str, operation_name: str, variables: dict):
        self.calls.append((operation_name, dict(variables)))
        if self.failure:
            raise self.failure
        if operation_name in {"MigrationOrganizations", "searchOrgs"}:
            return {
                "data": {
                    "organizations": [
                        {"id": "root", "name": "Provider"},
                        {"id": "child", "name": "Customer"},
                    ],
                    "managingOrg": {"id": "root", "name": "Provider"},
                }
            }
        if operation_name in {"MigrationWorkflowInventory", "getWorkflows"}:
            offset = variables["offset"]
            rows = (
                [
                    {
                        "id": "w1",
                        "name": "Onboard",
                        "orgId": "child",
                        "updatedAt": "2026-11-03T00:00:00Z",
                    }
                ]
                if offset == 0
                else []
            )
            return {"data": {"workflows": rows}}
        if operation_name in {"MigrationWorkflowDetail", "GET_WORKFLOW"}:
            return {
                "data": {
                    "workflow": {
                        "id": variables["id"],
                        "name": "Onboard",
                        "orgId": "child",
                        "tasks": [],
                    }
                }
            }
        if operation_name == "getFormsForOrg":
            return {"data": {"forms": []}}
        if operation_name in {"MigrationTemplates", "getTemplates"}:
            return {"data": {"templates": []}}
        if operation_name in {"MigrationVariables", "getVisibleOrgVariables"}:
            return {"data": {"visibleOrgVariables": []}}
        if operation_name in {
            "MigrationIntegrations",
            "getPacksAndBundlesByInstalledState",
        }:
            return {
                "data": {
                    "packsAndBundlesByInstalledState": {
                        "installedPacksAndBundles": [],
                        "marketplacePacksAndBundles": [],
                    }
                }
            }
        raise AssertionError(operation_name)


class MigrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = InMemoryMigrationStorage()
        self.service = MigrationService(
            storage=self.storage,
            root_org_id="root",
            api_url="https://api.rewst.io/graphql",
            graph_factory=lambda: FakeGraphQL(),
            now=lambda: "2026-11-03T12:00:00Z",
        )

    def _run(self, coroutine):
        return asyncio.run(coroutine)

    def test_sample_end_to_end_persists_resume_and_never_returns_session(self) -> None:
        created = self._run(
            self.service.create(
                name="Pilot", mode="sample", source_org_ids=["attacker"]
            )
        )
        migration_id = created["migration"]["id"]
        self.assertEqual(created["migration"]["mode"], "sample")
        self.assertEqual(created["migration"]["source_org_ids"], ["root"])

        exported = self._run(self.service.export_environment(migration_id))
        self.assertGreater(exported["artifact_count"], 0)
        context = self._run(self.service.get_context(migration_id))
        self.assertIn("sample", context["plan"]["markdown"].lower())
        item = context["items"][0]
        plan_artifact = self._run(
            self.service.read_artifact(migration_id, context["plan"]["artifact_id"])
        )
        self.assertEqual(plan_artifact["format"], "markdown")

        saved = self._run(
            self.service.save_plan(
                migration_id, "# Revised plan", context["plan"]["revision"]
            )
        )
        self.assertEqual(saved["plan"]["revision"], context["plan"]["revision"] + 1)
        updated = self._run(
            self.service.update_item(
                migration_id,
                item["id"],
                "implementing",
                "fixture replay ready",
                item["revision"],
            )
        )
        self.assertEqual(updated["item"]["status"], "implementing")
        resumed = self._run(self.service.get_context(migration_id))
        self.assertEqual(resumed["plan"]["markdown"], "# Revised plan")
        self.assertNotIn("appSession", json.dumps(resumed))
        self.assertNotIn("attacker", json.dumps(resumed))

    def test_sample_needs_no_configured_source_root(self) -> None:
        service = MigrationService(
            storage=InMemoryMigrationStorage(),
            root_org_id="",
            api_url="https://api.rewst.io/graphql",
            graph_factory=lambda: FakeGraphQL(),
            now=lambda: "2026-11-03T12:00:00Z",
        )
        created = self._run(service.create(name="Offline sample", mode="sample"))
        self.assertEqual(created["migration"]["source_org_ids"], ["sample-root"])

    def test_live_rejects_requested_scope_outside_discovered_tree(self) -> None:
        service = MigrationService(
            storage=InMemoryMigrationStorage(),
            root_org_id="root",
            api_url="https://api.rewst.io/graphql",
            graph_factory=lambda: FakeGraphQL(),
            now=lambda: "2026-11-03T12:00:00Z",
        )
        with self.assertRaisesRegex(RuntimeError, "outside configured scope"):
            self._run(
                service.create(
                    name="No tenant escape", mode="live", source_org_ids=["outsider"]
                )
            )

    def test_conflicts_and_evidence_gate_are_enforced(self) -> None:
        created = self._run(self.service.create(name="Pilot", mode="sample"))
        migration_id = created["migration"]["id"]
        self._run(self.service.export_environment(migration_id))
        context = self._run(self.service.get_context(migration_id))
        with self.assertRaisesRegex(ValueError, "changed"):
            self._run(
                self.service.save_plan(
                    migration_id, "# stale", context["plan"]["revision"] + 1
                )
            )
        item = context["items"][0]
        with self.assertRaisesRegex(ValueError, "evidence"):
            self._run(
                self.service.update_item(
                    migration_id, item["id"], "tested", "", item["revision"]
                )
            )

    def test_handoff_command_is_valid_shell_json_and_targets_this_install(self) -> None:
        service = MigrationService(
            storage=self.storage,
            root_org_id="",
            api_url="https://api.rewst.io/graphql",
            graph_factory=lambda: self.fail("sample must not access the network"),
            now=lambda: "2026-11-03T12:00:00Z",
            solution_id="installed-solution-id",
        )
        migration_id = self._run(service.create(name="Sample", mode="sample"))[
            "migration"
        ]["id"]
        self._run(service.export_environment(migration_id))
        context = self._run(service.get_context(migration_id))
        for command in context["handoff"]["commands"]:
            words = shlex.split(command)
            self.assertEqual(
                words[:4], ["bifrost", "api", "POST", "/api/workflows/execute"]
            )
            body = json.loads(words[4])
            self.assertEqual(body["solution_id"], "installed-solution-id")
            self.assertEqual(body["input_data"]["migration_id"], migration_id)
        self.assertEqual(len(context["handoff"]["commands"]), 3)
        plan_example = context["handoff"]["write_examples"][0]
        self.assertEqual(
            plan_example["request"]["input_data"]["expected_revision"],
            context["plan"]["revision"],
        )
        self.assertEqual(
            plan_example["request"]["solution_id"], "installed-solution-id"
        )
        self.assertTrue(plan_example["command"].endswith("@save-plan.json"))

    def test_successful_capture_is_immutable_and_progress_is_not_reset(self) -> None:
        migration_id = self._run(self.service.create(name="Pilot", mode="sample"))[
            "migration"
        ]["id"]
        self._run(self.service.export_environment(migration_id))
        context = self._run(self.service.get_context(migration_id))
        item = context["items"][0]
        self._run(
            self.service.update_item(
                migration_id,
                item["id"],
                "tested",
                "Sandbox replay passed",
                item["revision"],
            )
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self._run(self.service.export_environment(migration_id))
        self.assertEqual(
            self._run(self.service.get_context(migration_id))["items"][0]["status"],
            "tested",
        )

    def test_failed_export_remains_retryable_and_does_not_fabricate_empty_success(
        self,
    ) -> None:
        graph = FakeGraphQL()
        service = MigrationService(
            storage=self.storage,
            root_org_id="root",
            api_url="https://api.rewst.io/graphql",
            graph_factory=lambda: graph,
            now=lambda: "2026-11-03T12:00:00Z",
        )
        migration_id = self._run(
            service.create(name="Live", mode="live", source_org_ids=["child"])
        )["migration"]["id"]
        graph.failure = AuthorizationError("expired")
        before = len(graph.calls)
        with self.assertRaises(AuthorizationError):
            self._run(service.export_environment(migration_id))
        self.assertEqual(len(graph.calls), before + 1)
        context = self._run(service.get_context(migration_id))
        self.assertEqual(context["migration"]["status"], "draft")
        self.assertEqual(context["items"], [])
        graph.failure = None
        self._run(service.export_environment(migration_id))

    def test_capture_has_dependency_evidence_and_nonworkflow_work_items(self) -> None:
        migration_id = self._run(self.service.create(name="Pilot", mode="sample"))[
            "migration"
        ]["id"]
        self._run(self.service.export_environment(migration_id))
        context = self._run(self.service.get_context(migration_id))
        self.assertIn("integrations", {item["kind"] for item in context["items"]})
        graph = next(
            artifact
            for artifact in context["artifacts"]
            if artifact["kind"] == "dependency_graph"
        )
        content = self._run(self.service.read_artifact(migration_id, graph["id"]))[
            "content"
        ]
        self.assertEqual(content["edges"][0]["kind"], "subworkflow")
        self.assertTrue(any(row["status"] != "complete" for row in context["coverage"]))

    def test_live_rejects_unapproved_tenant_and_marks_partial_coverage(self) -> None:
        graph = FakeGraphQL()
        service = MigrationService(
            storage=InMemoryMigrationStorage(),
            root_org_id="root",
            api_url="https://api.rewst.io/graphql",
            graph_factory=lambda: graph,
            now=lambda: "2026-11-03T12:00:00Z",
        )
        created = self._run(
            service.create(name="Live", mode="live", source_org_ids=["child"])
        )
        migration_id = created["migration"]["id"]
        exported = self._run(service.export_environment(migration_id))
        self.assertEqual(exported["migration"]["source_org_ids"], ["child"])
        self.assertTrue(
            any(row["status"] == "metadata_only" for row in exported["coverage"])
        )
        self.assertTrue(
            all("appSession" not in json.dumps(call) for call in graph.calls)
        )

    def test_auth_and_schema_errors_are_sanitized(self) -> None:
        for error, expected in [
            (AuthorizationError("cookie=secret"), "authorization"),
            (SchemaCompatibilityError("field appSession"), "schema"),
        ]:
            service = MigrationService(
                storage=InMemoryMigrationStorage(),
                root_org_id="root",
                api_url="https://api.rewst.io/graphql",
                graph_factory=lambda error=error: FakeGraphQL(failure=error),
                now=lambda: "2026-11-03T12:00:00Z",
            )
            with self.assertRaisesRegex(RuntimeError, expected):
                self._run(service.create(name="Live", mode="live"))


if __name__ == "__main__":
    unittest.main()
