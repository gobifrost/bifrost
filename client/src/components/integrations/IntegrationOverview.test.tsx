/**
 * Component tests for IntegrationOverview.
 *
 * Covers the OAuth card's status branches — connected vs. unconfigured vs.
 * expired — plus the Connect/Refresh/Create button wiring and the defaults
 * editor affordance.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { IntegrationOverview } from "./IntegrationOverview";

function renderOverview(
	overrides: Partial<Parameters<typeof IntegrationOverview>[0]> = {},
) {
	const onOpenDefaultsDialog = vi.fn();
	const onOAuthConnect = vi.fn();
	const onOAuthRefresh = vi.fn();
	const onEditOAuthConfig = vi.fn();
	const onDeleteOAuthConfig = vi.fn();
	const onCreateOAuthConfig = vi.fn();

	const utils = renderWithProviders(
		<IntegrationOverview
			integration={{
				name: "Test",
				has_oauth_config: false,
				config_schema: [],
				config_defaults: {},
				default_entity_id: "common",
				entity_id_name: "Tenant ID",
			}}
			oauthConfig={null}
			isOAuthConnected={false}
			isOAuthExpired={false}
			isOAuthExpiringSoon={false}
			canUseAuthCodeFlow
			onOpenDefaultsDialog={onOpenDefaultsDialog}
			onOAuthConnect={onOAuthConnect}
			onOAuthRefresh={onOAuthRefresh}
			onEditOAuthConfig={onEditOAuthConfig}
			onDeleteOAuthConfig={onDeleteOAuthConfig}
			onCreateOAuthConfig={onCreateOAuthConfig}
			isAuthorizePending={false}
			isRefreshPending={false}
			{...overrides}
		/>,
	);
	return {
		...utils,
		onOpenDefaultsDialog,
		onOAuthConnect,
		onOAuthRefresh,
		onEditOAuthConfig,
		onDeleteOAuthConfig,
		onCreateOAuthConfig,
	};
}

describe("IntegrationOverview — no OAuth configured", () => {
	it("shows 'No OAuth configured' and fires Configure handler", async () => {
		const { user, onCreateOAuthConfig } = renderOverview();

		expect(screen.getByText(/no oauth configured/i)).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /configure/i }));
		expect(onCreateOAuthConfig).toHaveBeenCalledTimes(1);
	});

	it("opens the defaults editor via the edit-defaults pencil button", async () => {
		const { user, onOpenDefaultsDialog } = renderOverview();

		await user.click(
			screen.getByRole("button", { name: /edit default values/i }),
		);
		expect(onOpenDefaultsDialog).toHaveBeenCalledTimes(1);
	});
});

describe("IntegrationOverview — connected", () => {
	it("renders Connected status, Reconnect default button, and Refresh default token", async () => {
		const { user, onOAuthConnect, onOAuthRefresh } = renderOverview({
			integration: {
				name: "Test",
				has_oauth_config: true,
				config_schema: [],
				config_defaults: {},
				default_entity_id: null,
				entity_id_name: null,
			},
			oauthConfig: {
				status: "connected",
				expires_at: "2030-01-01T00:00:00Z",
				oauth_flow_type: "authorization_code",
				has_refresh_token: true,
			},
			isOAuthConnected: true,
		});

		expect(screen.getByText("Connected")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /reconnect default/i }));
		expect(onOAuthConnect).toHaveBeenCalledTimes(1);

		await user.click(screen.getByRole("button", { name: /refresh default token/i }));
		expect(onOAuthRefresh).toHaveBeenCalledTimes(1);
	});

	it("warns about token expiry when isOAuthExpired is true", () => {
		renderOverview({
			integration: {
				name: "Test",
				has_oauth_config: true,
				config_schema: [],
				config_defaults: {},
				default_entity_id: null,
				entity_id_name: null,
			},
			oauthConfig: {
				status: "connected",
				oauth_flow_type: "authorization_code",
			},
			isOAuthConnected: false,
			isOAuthExpired: true,
		});
		expect(
			screen.getByText(/token expired - reconnect required/i),
		).toBeInTheDocument();
	});
});

describe("IntegrationOverview — client_credentials flow", () => {
	it("shows 'Get Token' for client_credentials when not connected", async () => {
		const { user, onOAuthRefresh } = renderOverview({
			integration: {
				name: "Test",
				has_oauth_config: true,
				config_schema: [],
				config_defaults: {},
				default_entity_id: null,
				entity_id_name: null,
			},
			oauthConfig: {
				status: "pending",
				oauth_flow_type: "client_credentials",
			},
			isOAuthConnected: false,
			canUseAuthCodeFlow: false,
		});

		await user.click(screen.getByRole("button", { name: /get token/i }));
		expect(onOAuthRefresh).toHaveBeenCalledTimes(1);
	});
});

describe("IntegrationOverview — default fallback helper text", () => {
	it("shows helper text under the connection button explaining it's the default fallback", () => {
		renderOverview({
			integration: {
				name: "Test",
				has_oauth_config: true,
				config_schema: [],
				config_defaults: {},
				default_entity_id: null,
				entity_id_name: null,
			},
			oauthConfig: {
				status: "connected",
				oauth_flow_type: "authorization_code",
				has_refresh_token: true,
			},
			isOAuthConnected: true,
		});
		expect(
			screen.getByText(/used when an organization isn't individually connected/i),
		).toBeInTheDocument();
	});
});
