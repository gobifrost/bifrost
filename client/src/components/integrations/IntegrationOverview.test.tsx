/**
 * Component tests for the integration status chips and OAuth alert:
 * OAuth states (not set up, connected, expired, client credentials), their
 * actions, the defaults summary and popover, and the defaults editor.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import {
	IntegrationOAuthAlert,
	IntegrationStatusChips,
	type IntegrationOverviewProps,
} from "./IntegrationOverview";

function renderOverview(overrides: Partial<IntegrationOverviewProps> = {}) {
	const handlers = {
		onOpenDefaultsDialog: vi.fn(),
		onOAuthConnect: vi.fn(),
		onOAuthRefresh: vi.fn(),
		onEditOAuthConfig: vi.fn(),
		onDeleteOAuthConfig: vi.fn(),
		onCreateOAuthConfig: vi.fn(),
	};
	const props: IntegrationOverviewProps = {
		integration: {
			name: "Test",
			has_oauth_config: false,
			config_schema: [],
			config_defaults: {},
			default_entity_id: "common",
			entity_id_name: "Tenant ID",
		},
		oauthConfig: null,
		isOAuthConnected: false,
		isOAuthExpired: false,
		isOAuthExpiringSoon: false,
		canUseAuthCodeFlow: true,
		isAuthorizePending: false,
		isRefreshPending: false,
		canManageDefinitions: true,
		...handlers,
		...overrides,
	};
	const utils = renderWithProviders(
		<>
			<IntegrationStatusChips {...props} />
			<IntegrationOAuthAlert {...props} />
		</>,
	);
	return { ...utils, ...handlers };
}

const connected = {
	name: "Test",
	has_oauth_config: true,
	config_schema: [],
	config_defaults: {},
	default_entity_id: null,
	entity_id_name: null,
};

describe("IntegrationStatusChips — OAuth", () => {
	it("offers set-up from the chip when OAuth is not configured", async () => {
		const { user, onCreateOAuthConfig } = renderOverview();

		await user.click(
			screen.getByRole("button", { name: "Default OAuth: Not Set Up" }),
		);
		await user.click(screen.getByRole("button", { name: /set up oauth/i }));
		expect(onCreateOAuthConfig).toHaveBeenCalledTimes(1);
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
	});

	it("shows a connected chip whose popover reconnects, refreshes and explains the fallback", async () => {
		const { user, onOAuthConnect, onOAuthRefresh } = renderOverview({
			integration: connected,
			oauthConfig: {
				status: "completed",
				expires_at: "2030-01-01T00:00:00Z",
				oauth_flow_type: "authorization_code",
				has_refresh_token: true,
			},
			isOAuthConnected: true,
		});

		await user.click(
			screen.getByRole("button", { name: "Default OAuth: Connected" }),
		);
		expect(screen.getByText("Authorization Code")).toBeInTheDocument();
		expect(
			screen.getByText(
				/fallback for organizations without their own connection/i,
			),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: /reconnect default/i }),
		);
		expect(onOAuthConnect).toHaveBeenCalledTimes(1);
		await user.click(
			screen.getByRole("button", { name: /refresh default token/i }),
		);
		expect(onOAuthRefresh).toHaveBeenCalledTimes(1);
	});

	it("raises an alert with the fix when the default token expired", async () => {
		const { user, onOAuthConnect } = renderOverview({
			integration: connected,
			oauthConfig: {
				status: "completed",
				oauth_flow_type: "authorization_code",
			},
			isOAuthConnected: true,
			isOAuthExpired: true,
		});

		expect(
			screen.getByRole("button", { name: "Default OAuth: Expired" }),
		).toBeInTheDocument();
		const alert = screen.getByRole("alert");
		expect(alert).toHaveTextContent(/default oauth expired/i);
		await user.click(
			within(alert).getByRole("button", { name: /reconnect default/i }),
		);
		expect(onOAuthConnect).toHaveBeenCalledTimes(1);
	});

	it("offers Get Token for client credentials that are not connected", async () => {
		const { user, onOAuthRefresh } = renderOverview({
			integration: connected,
			oauthConfig: {
				status: "pending",
				oauth_flow_type: "client_credentials",
			},
			canUseAuthCodeFlow: false,
		});

		await user.click(
			screen.getByRole("button", { name: /default oauth/i }),
		);
		await user.click(screen.getByRole("button", { name: /get token/i }));
		expect(onOAuthRefresh).toHaveBeenCalledTimes(1);
	});
});

describe("IntegrationStatusChips — defaults", () => {
	it("summarizes defaults and lists them with secrets masked", async () => {
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

		const chip = screen.getByRole("button", { name: "Defaults" });
		expect(chip).toHaveTextContent("Tenant ID: common · 2 of 3 defaults");
		await user.click(chip);
		expect(screen.getByText("us")).toBeInTheDocument();
		expect(screen.queryByText("hunter2")).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /configure default values/i }),
		).not.toBeInTheDocument();
	});

	it("opens the defaults editor for managers", async () => {
		const { user, onOpenDefaultsDialog } = renderOverview();

		await user.click(screen.getByRole("button", { name: "Defaults" }));
		await user.click(
			screen.getByRole("button", { name: /configure default values/i }),
		);
		expect(onOpenDefaultsDialog).toHaveBeenCalledTimes(1);
	});
});
