"""Deploy pre-creates EMPTY integration shells for declared connections.

Task 5: for each declared connection whose global ``Integration`` doesn't yet
exist, deploy CREATES an empty integration shell (Integration row + its
IntegrationConfigSchema rows + an OAuthProvider skeleton with empty
client_id/secret). If the integration already exists, NO-OP — never clobber a
configured integration. The admin then fills in credentials.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm.integrations import Integration, IntegrationConfigSchema
from src.models.orm.oauth import OAuthProvider
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionDeployer


@pytest.mark.e2e
class TestSolutionDeployShells:
    async def test_creates_shell_when_absent(self, db_session) -> None:
        db = db_session
        name = f"NewInteg-{uuid4().hex[:8]}"
        decls = [{
            "integration_name": name,
            "template": {
                "name": name,
                "entity_id_name": "tenant_id",
                "default_entity_id": "common",
                "config_schema": [{
                    "key": "url", "type": "string", "required": True,
                    "description": None, "options": None, "position": 0,
                }],
                "oauth": {
                    "provider_name": f"p-{name}", "display_name": "P",
                    "oauth_flow_type": "authorization_code",
                    "authorization_url": "https://a", "token_url": "https://t",
                    "audience": None, "token_url_defaults": {},
                    "entity_id_source": None, "scopes": [], "redirect_uri": None,
                },
            },
        }]
        created = await SolutionDeployer(db)._upsert_integration_shells(decls)
        await db.flush()
        assert created == 1

        integ = (await db.execute(
            select(Integration).where(Integration.name == name)
        )).scalar_one()
        assert integ.entity_id_name == "tenant_id"
        assert integ.default_entity_id == "common"

        schema = (await db.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == integ.id
            )
        )).scalars().all()
        assert len(schema) == 1
        assert schema[0].key == "url" and schema[0].required is True

        provider = (await db.execute(
            select(OAuthProvider).where(OAuthProvider.integration_id == integ.id)
        )).scalar_one()
        assert provider.client_id == ""  # empty shell, no secret
        assert provider.encrypted_client_secret == b""
        assert provider.status == "not_connected"

    async def test_noop_when_integration_exists(self, db_session) -> None:
        db = db_session
        name = f"Existing-{uuid4().hex[:8]}"
        db.add(Integration(name=name))
        await db.flush()
        created = await SolutionDeployer(db)._upsert_integration_shells(
            [{"integration_name": name, "template": {
                "name": name, "config_schema": [], "oauth": None}}]
        )
        await db.flush()
        assert created == 0  # never clobber

        # No config schema or oauth provider was attached to the existing row.
        integ = (await db.execute(
            select(Integration).where(Integration.name == name)
        )).scalar_one()
        schema = (await db.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == integ.id
            )
        )).scalars().all()
        assert schema == []
        provider = (await db.execute(
            select(OAuthProvider).where(OAuthProvider.integration_id == integ.id)
        )).scalar_one_or_none()
        assert provider is None

    async def test_intra_bundle_duplicate_name_creates_one(self, db_session) -> None:
        # The same declaration twice in one bundle dedups to a single shell —
        # the per-decl flush makes the second pass see the row the first created.
        # Pins that dedup so a future flush refactor can't silently break it.
        db = db_session
        name = f"DupInteg-{uuid4().hex[:8]}"
        decl = {"integration_name": name, "template": {
            "name": name, "config_schema": [], "oauth": None}}
        created = await SolutionDeployer(db)._upsert_integration_shells([decl, decl])
        await db.flush()
        assert created == 1
        rows = (await db.execute(
            select(Integration).where(Integration.name == name)
        )).scalars().all()
        assert len(rows) == 1

    async def test_two_shells_same_provider_name_no_error(self, db_session) -> None:
        # Two DIFFERENT integrations whose OAuth providers share a provider_name.
        # Both shells are global (organization_id NULL), and the unique index
        # (organization_id, provider_name) treats NULLs as distinct in Postgres,
        # so this never collides — documents the design intent.
        db = db_session
        n1 = f"ShellA-{uuid4().hex[:8]}"
        n2 = f"ShellB-{uuid4().hex[:8]}"
        shared = "shared-provider"
        decls = [
            {"integration_name": nm, "template": {
                "name": nm, "config_schema": [],
                "oauth": {"provider_name": shared, "display_name": "S",
                          "oauth_flow_type": "authorization_code",
                          "authorization_url": "https://a", "token_url": "https://t",
                          "audience": None, "token_url_defaults": {},
                          "entity_id_source": None, "scopes": [], "redirect_uri": None}}}
            for nm in (n1, n2)
        ]
        created = await SolutionDeployer(db)._upsert_integration_shells(decls)
        await db.flush()
        assert created == 2
        providers = (await db.execute(
            select(OAuthProvider).where(OAuthProvider.provider_name == shared)
        )).scalars().all()
        assert len(providers) == 2

    async def test_connection_declarations_full_replace_removes_stale(
        self, db_session
    ) -> None:
        # _upsert_connection_declarations is deploy-owned FULL-REPLACE (unlike the
        # capture writer it mirrors, which only upserts). A re-deploy whose bundle
        # DROPS a connection must delete the now-stale SolutionConnectionSchema row.
        db = db_session
        dep = SolutionDeployer(db)
        slug = f"conn-decl-{uuid4().hex[:8]}"
        sol = Solution(id=uuid4(), slug=slug, name=slug.upper(), organization_id=None)
        db.add(sol)
        await db.flush()

        # First deploy: two declarations.
        await dep._upsert_connection_declarations(sol, [
            {"integration_name": "Keep", "position": 0,
             "template": {"name": "Keep", "config_schema": [], "oauth": None}},
            {"integration_name": "Drop", "position": 1,
             "template": {"name": "Drop", "config_schema": [], "oauth": None}},
        ])
        await db.flush()
        rows = (await db.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == sol.id
            )
        )).scalars().all()
        assert {r.integration_name for r in rows} == {"Keep", "Drop"}

        # Re-deploy with only "Keep" — "Drop" must be reconciled away.
        await dep._upsert_connection_declarations(sol, [
            {"integration_name": "Keep", "position": 0,
             "template": {"name": "Keep", "config_schema": [], "oauth": None}},
        ])
        await db.flush()
        rows = (await db.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == sol.id
            )
        )).scalars().all()
        assert {r.integration_name for r in rows} == {"Keep"}  # Drop reconciled away

    async def test_no_oauth_template_creates_no_provider(self, db_session) -> None:
        db = db_session
        name = f"NoOAuth-{uuid4().hex[:8]}"
        created = await SolutionDeployer(db)._upsert_integration_shells(
            [{"integration_name": name, "template": {
                "name": name, "config_schema": [], "oauth": None}}]
        )
        await db.flush()
        assert created == 1
        integ = (await db.execute(
            select(Integration).where(Integration.name == name)
        )).scalar_one()
        provider = (await db.execute(
            select(OAuthProvider).where(OAuthProvider.integration_id == integ.id)
        )).scalar_one_or_none()
        assert provider is None
