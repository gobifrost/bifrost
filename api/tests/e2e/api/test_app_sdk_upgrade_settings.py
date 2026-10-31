"""End-to-end coverage for automatic App SDK upgrade settings."""

import pytest

SETTINGS = "/api/maintenance/app-sdk-upgrades/settings"


@pytest.mark.e2e
class TestAppSdkUpgradeSettingsApi:
    def test_platform_admin_reads_updates_and_audits_global_setting(
        self, e2e_client, platform_admin
    ) -> None:
        initial = e2e_client.get(SETTINGS, headers=platform_admin.headers)
        assert initial.status_code == 200, initial.text
        initial_settings = initial.json()
        assert initial_settings == {"enabled": False}
        initial_enabled = initial_settings["enabled"]

        existing_events = e2e_client.get(
            "/api/audit?action=settings.app_sdk_upgrades.update",
            headers=platform_admin.headers,
        )
        assert existing_events.status_code == 200, existing_events.text
        existing_event_ids = {
            entry["id"] for entry in existing_events.json()["entries"]
        }

        try:
            updated = e2e_client.put(
                SETTINGS, json={"enabled": True}, headers=platform_admin.headers
            )
            assert updated.status_code == 200, updated.text
            assert updated.json() == {"enabled": True}

            events = e2e_client.get(
                "/api/audit?action=settings.app_sdk_upgrades.update",
                headers=platform_admin.headers,
            )
            assert events.status_code == 200, events.text
            new_events = [
                entry
                for entry in events.json()["entries"]
                if entry["id"] not in existing_event_ids
            ]
            assert len(new_events) == 1, events.text
            event = new_events[0]
            assert event["action"] == "settings.app_sdk_upgrades.update"
            assert event["resource_type"] == "system_config"
            assert event["details"] == {
                "before": {"enabled": False},
                "after": {"enabled": True},
            }
        finally:
            restored = e2e_client.put(
                SETTINGS,
                json={"enabled": initial_enabled},
                headers=platform_admin.headers,
            )
            assert restored.status_code == 200, restored.text
            assert restored.json() == {"enabled": initial_enabled}

    def test_non_admin_cannot_read_or_update_global_setting(
        self, e2e_client, org1_user
    ) -> None:
        assert e2e_client.get(SETTINGS, headers=org1_user.headers).status_code == 403
        assert (
            e2e_client.put(
                SETTINGS, json={"enabled": True}, headers=org1_user.headers
            ).status_code
            == 403
        )
