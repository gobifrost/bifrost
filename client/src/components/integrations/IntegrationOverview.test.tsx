/**
 * Component tests for IntegrationOverview.
 *
 * Covers the Default OAuth row's status branches — connected vs.
 * unconfigured vs. expired — plus the Connect/Refresh/Create button wiring,
 * the defaults summary and popover, and the defaults editor affordance.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
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
			canManageDefinitions
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
	it("shows Not Configured and fires the set-up handler", async () => {
		const { user, onCreateOAuthConfig } = renderOverview();

		const oauth = screen.getByRole("region", { name: "Default OAuth" });
		expect(within(oauth).getByText("Not Configured")).toBeInTheDocument();
		await user.click(
			within(oauth).getByRole("button", { name: /set up oauth/i }),
		);
		expect(onCreateOAuthConfig).toHaveBeenCalledTimes(1);
	});

	it("opens the defaults editor via the configure button", async () => {
		const { user, onOpenDefaultsDialog } = renderOverview();

		await user.click(
			screen.getByRole("button", { name: /configure default values/i }),
		);
		expect(onOpenDefaultsDialog).toHaveBeenCalledTimes(1);
	});

	it("summarizes defaults and lists them, secrets masked, on demand", async () => {
		const { user } = renderOverview({
			canManageDefinitions: false,
			integration: {
				name: "Test",
				has_oauth_config: false,
				config_schema: [
					{ key: "region", type: "string" },
					{ key: "api_key", type: "secret" },
					{ key: "page_size", type: "int" },
				],
				config_defaults: { region: "us", api_key: "hunter2" },
				default_entity_id: "common",
				entity_id_name: "Tenant ID",
			},
		});

		const defaults = screen.getByRole("region", { name: "Defaults" });
		expect(within(defaults).getByText("common")).toBeInTheDocument();
		expect(
			within(defaults).getByText(/2 of 3 config defaults set/),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /configure default values/i }),
		).not.toBeInTheDocument();

		await user.click(
			within(defaults).getByRole("button", { name: /view defaults/i }),
		);
		expect(screen.getByText("us")).toBeInTheDocument();
		expect(screen.queryByText("hunter2")).not.toBeInTheDocument();
		expect(
			screen.getByText(
				"\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022",
			),
		).toBeInTheDocument();
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

		await user.click(
			screen.getByRole("button", { name: /reconnect default/i }),
		);
		expect(onOAuthConnect).toHaveBeenCalledTimes(1);

		await user.click(
			screen.getByRole("button", { name: /refresh default token/i }),
		);
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
			isOAuthConnected: true,
			isOAuthExpired: true,
		});
		expect(
			screen.getByText(/token expired - reconnect required/i),
		).toBeInTheDocument();
		expect(
			screen.getByText("Expired", { exact: true }),
		).toBeInTheDocument();
		expect(
			screen.queryByText("Connected", { exact: true }),
		).not.toBeInTheDocument();
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
			screen.getByText(
				/fallback for organizations without their own connection/i,
			),
		).toBeInTheDocument();
	});
});
