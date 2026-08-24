"""Live API coverage for export/import authorization dependencies."""

import io

import pytest

from src.models.contracts.export_import import ConfigExportFile


@pytest.mark.e2e
class TestExportImportAuthorization:
    def test_platform_admin_can_export_configs(
        self,
        e2e_client,
        platform_admin,
    ) -> None:
        response = e2e_client.post(
            "/api/export-import/export/configs",
            headers=platform_admin.headers,
            json={"ids": []},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_org_user_without_import_capability_is_denied(
        self,
        e2e_client,
        org1_user,
    ) -> None:
        export = ConfigExportFile(
            item_count=1,
            items=[
                {
                    "key": "blocked_import",
                    "value": "value",
                    "config_type": "string",
                }
            ],
        )
        response = e2e_client.post(
            "/api/export-import/import/configs",
            headers={"Authorization": f"Bearer {org1_user.access_token}"},
            files={
                "file": (
                    "configs.json",
                    io.BytesIO(export.model_dump_json().encode()),
                    "application/json",
                )
            },
        )

        assert response.status_code == 403
