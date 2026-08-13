"""Live API coverage for private-memory defaults and instruction delivery."""


class TestMemoryAPI:
    def test_memory_defaults_on_for_users_and_supports_opt_out(
        self,
        e2e_client,
        platform_admin,
        org1_user,
    ):
        disabled = e2e_client.put(
            "/api/admin/memory/settings",
            headers=platform_admin.headers,
            json={"enabled": False},
        )
        assert disabled.status_code == 200

        user_default = e2e_client.get(
            "/api/memory/settings",
            headers=org1_user.headers,
        )
        assert user_default.status_code == 200
        assert user_default.json() == {
            "platform_enabled": False,
            "user_enabled": True,
            "effective_enabled": False,
        }

        enabled = e2e_client.put(
            "/api/admin/memory/settings",
            headers=platform_admin.headers,
            json={"enabled": True},
        )
        assert enabled.status_code == 200

        user_enabled = e2e_client.get(
            "/api/memory/settings",
            headers=org1_user.headers,
        )
        assert user_enabled.status_code == 200
        assert user_enabled.json() == {
            "platform_enabled": True,
            "user_enabled": True,
            "effective_enabled": True,
        }

        instructions = e2e_client.get(
            "/api/required-instructions",
            headers=org1_user.headers,
        )
        assert instructions.status_code == 200
        assert instructions.json()["instructions"][0].startswith("# Memory")
        assert "do not save secrets" in instructions.json()["instructions"][0]

        memories = e2e_client.get("/api/memory", headers=org1_user.headers)
        assert memories.status_code == 200
        assert memories.json() == {"entries": [], "count": 0}

        opted_out = e2e_client.put(
            "/api/memory/settings",
            headers=org1_user.headers,
            json={"enabled": False},
        )
        assert opted_out.status_code == 200
        assert opted_out.json()["effective_enabled"] is False
        e2e_client.put(
            "/api/admin/memory/settings",
            headers=platform_admin.headers,
            json={"enabled": False},
        )

    def test_platform_setting_requires_admin(
        self,
        e2e_client,
        org1_user,
    ):
        response = e2e_client.put(
            "/api/admin/memory/settings",
            headers=org1_user.headers,
            json={"enabled": True},
        )
        assert response.status_code == 403

    def test_memory_endpoints_require_authentication(self, e2e_api_url):
        import httpx

        with httpx.Client(base_url=e2e_api_url, timeout=30.0) as client:
            assert client.get("/api/memory/settings").status_code == 401
            assert client.get("/api/memory").status_code == 401
            assert client.get("/api/required-instructions").status_code == 401

    def test_required_instructions_resolve_global_and_organization_sections(
        self,
        e2e_client,
        platform_admin,
        org1,
        org1_user,
        org2_user,
    ):
        global_update = e2e_client.put(
            "/api/admin/required-instructions",
            headers=platform_admin.headers,
            json={"instructions": "Confirm all destructive actions."},
        )
        assert global_update.status_code == 200

        organization_update = e2e_client.put(
            f"/api/admin/required-instructions/organizations/{org1['id']}",
            headers=platform_admin.headers,
            json={"instructions": "Use the Acme runbook."},
        )
        assert organization_update.status_code == 200

        try:
            org1_response = e2e_client.get(
                "/api/required-instructions",
                headers=org1_user.headers,
            )
            assert org1_response.status_code == 200
            org1_instructions = org1_response.json()["instructions"]
            assert "# Global Instructions\n\nConfirm all destructive actions." in org1_instructions
            assert "# Organization Instructions\n\nUse the Acme runbook." in org1_instructions

            org2_response = e2e_client.get(
                "/api/required-instructions",
                headers=org2_user.headers,
            )
            assert org2_response.status_code == 200
            org2_instructions = org2_response.json()["instructions"]
            assert "# Global Instructions\n\nConfirm all destructive actions." in org2_instructions
            assert not any(
                item.startswith("# Organization Instructions")
                for item in org2_instructions
            )
        finally:
            e2e_client.put(
                f"/api/admin/required-instructions/organizations/{org1['id']}",
                headers=platform_admin.headers,
                json={"instructions": ""},
            )
            e2e_client.put(
                "/api/admin/required-instructions",
                headers=platform_admin.headers,
                json={"instructions": ""},
            )

    def test_required_instruction_settings_require_platform_admin(
        self,
        e2e_client,
        org1,
        org1_user,
    ):
        global_response = e2e_client.put(
            "/api/admin/required-instructions",
            headers=org1_user.headers,
            json={"instructions": "Not allowed"},
        )
        organization_response = e2e_client.put(
            f"/api/admin/required-instructions/organizations/{org1['id']}",
            headers=org1_user.headers,
            json={"instructions": "Not allowed"},
        )
        assert global_response.status_code == 403
        assert organization_response.status_code == 403
