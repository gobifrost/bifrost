/**
 * Component tests for IntegrationMappingsTab.
 *
 * Covers the three user-visible states: no data provider, no orgs, and the
 * populated records — plus the Delete and Edit callbacks. EntitySelector +
 * AutoMatchControls are rendered as-is since they're purely callback-driven.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { IntegrationMappingsTab } from "./IntegrationMappingsTab";
import type { OrgWithMapping } from "./IntegrationMappingsTab";
import type { IntegrationMapping } from "@/services/integrations";

function renderTab(
	overrides: Partial<Parameters<typeof IntegrationMappingsTab>[0]> = {},
) {
	const handlers = {
		onRunAutoMatch: vi.fn(),
		onAcceptAllSuggestions: vi.fn(),
		onClearSuggestions: vi.fn(),
		onAcceptSuggestion: vi.fn(),
		onRejectSuggestion: vi.fn(),
		onUpdateOrgMapping: vi.fn(),
		onOpenConfigDialog: vi.fn(),
		onDeleteMapping: vi.fn(),
		onConnectMapping: vi.fn(),
		onDisconnectMapping: vi.fn(),
		onRefreshMapping: vi.fn(),
	};
	const utils = renderWithProviders(
		<IntegrationMappingsTab
			orgsWithMappings={[]}
			entities={[]}
			isLoadingEntities={false}
			hasDataProvider={true}
			hasOAuth={false}
			configSchema={[]}
			configDefaults={{}}
			autoMatchSuggestions={new Map()}
			matchStats={null}
			isMatching={false}
			isDeletePending={false}
			{...handlers}
			{...overrides}
		/>,
	);
	return { ...utils, ...handlers };
}

describe("IntegrationMappingsTab — empty states", () => {
	it("shows 'No organizations available' when no orgs were passed in (with data provider)", () => {
		renderTab({ orgsWithMappings: [] });
		expect(
			screen.getByText(/no organizations available/i),
		).toBeInTheDocument();
	});

	it("shows 'No organizations available' when no orgs and no data provider", () => {
		renderTab({ hasDataProvider: false, orgsWithMappings: [] });
		expect(
			screen.getByText(/no organizations available/i),
		).toBeInTheDocument();
	});
});

describe("IntegrationMappingsTab — populated", () => {
	const orgs: OrgWithMapping[] = [
		{
			id: "org-1",
			name: "Acme",
			mapping: {
				id: "map-1",
				integration_id: "int-1",
				organization_id: "org-1",
				entity_id: "ent-a",
				entity_name: "Entity A",
				oauth_token_id: null,
				config: {},
			} as unknown as OrgWithMapping["mapping"],
			formData: {
				organization_id: "org-1",
				entity_id: "ent-a",
				entity_name: "Entity A",
				config: {},
			},
		},
		{
			id: "org-2",
			name: "Beta",
			mapping: undefined,
			formData: {
				organization_id: "org-2",
				entity_id: "",
				entity_name: "",
				config: {},
			},
		},
	];

	it("renders a row for each organization with the right status badge", () => {
		renderTab({
			orgsWithMappings: orgs,
			entities: [
				{ value: "ent-a", label: "Entity A" },
				{ value: "ent-b", label: "Entity B" },
			],
		});

		expect(screen.getByText("Acme")).toBeInTheDocument();
		expect(screen.getByText("Beta")).toBeInTheDocument();
		// Acme is mapped
		expect(screen.getByText("Mapped")).toBeInTheDocument();
		// Beta has no mapping and no entity_id -> Not Mapped
		expect(screen.getByText("Not Mapped")).toBeInTheDocument();
	});

	it("keeps the entity selector searchable inside a compact mapping row", async () => {
		const { user, onUpdateOrgMapping } = renderTab({
			orgsWithMappings: orgs,
			entities: [
				{ value: "ent-a", label: "Entity A" },
				{ value: "ent-b", label: "Entity B" },
			],
		});

		const betaRow = screen.getByText("Beta").closest("li")!;
		await user.click(within(betaRow).getByRole("combobox"));

		const search = screen.getByRole("combobox", {
			name: "Search entities...",
		});
		await user.type(search, "Entity B");
		await user.click(screen.getByRole("option", { name: /Entity B/i }));

		expect(onUpdateOrgMapping).toHaveBeenCalledWith(
			"org-2",
			"ent-b",
			"Entity B",
		);
	});

	it("shows auto-match controls when a data provider is available", async () => {
		const { user, onRunAutoMatch } = renderTab({
			orgsWithMappings: orgs,
			entities: [
				{ value: "ent-a", label: "Entity A" },
				{ value: "ent-b", label: "Entity B" },
			],
		});

		await user.click(
			screen.getByRole("button", { name: /auto-match unmapped/i }),
		);
		expect(onRunAutoMatch).toHaveBeenCalledWith("exact");
	});

	it("filters mappings by organization and entity labels", async () => {
		const { user } = renderTab({
			orgsWithMappings: orgs,
			entities: [
				{ value: "ent-a", label: "Entity A" },
				{ value: "ent-b", label: "Entity B" },
			],
		});

		await user.type(
			screen.getByRole("searchbox", {
				name: "Search organization mappings",
			}),
			"Entity A",
		);

		expect(screen.getByText("1 of 2 mappings")).toBeInTheDocument();
		expect(screen.getByText("Acme")).toBeInTheDocument();
		expect(screen.queryByText("Beta")).not.toBeInTheDocument();
	});

	it("clears an empty mapping search state", async () => {
		const { user } = renderTab({
			orgsWithMappings: orgs,
			entities: [{ value: "ent-a", label: "Entity A" }],
		});

		await user.type(
			screen.getByRole("searchbox", {
				name: "Search organization mappings",
			}),
			"missing",
		);

		expect(
			screen.getByText("No mappings match your search"),
		).toBeInTheDocument();
		expect(screen.getByText("0 of 2 mappings")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Clear search" }));

		expect(screen.getByText("2 mappings")).toBeInTheDocument();
		expect(screen.getByText("Acme")).toBeInTheDocument();
		expect(screen.getByText("Beta")).toBeInTheDocument();
	});

	it("does not scope auto-match to the current mapping search", async () => {
		const { user, onRunAutoMatch } = renderTab({
			orgsWithMappings: orgs,
			entities: [
				{ value: "ent-a", label: "Entity A" },
				{ value: "ent-b", label: "Entity B" },
			],
		});

		await user.type(
			screen.getByRole("searchbox", {
				name: "Search organization mappings",
			}),
			"Acme",
		);
		await user.click(
			screen.getByRole("button", { name: /auto-match unmapped/i }),
		);

		expect(screen.getByText("1 of 2 mappings")).toBeInTheDocument();
		expect(onRunAutoMatch).toHaveBeenCalledWith("exact");
	});

	it("fires onDeleteMapping for the org whose Unlink button is clicked", async () => {
		const { user, onDeleteMapping } = renderTab({
			orgsWithMappings: orgs,
			entities: [{ value: "ent-a", label: "Entity A" }],
		});

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Acme" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Unlink" }));
		expect(onDeleteMapping).toHaveBeenCalledWith(orgs[0]);
	});

	it("opens the config dialog from the row actions menu", async () => {
		const { user, onOpenConfigDialog } = renderTab({
			orgsWithMappings: orgs,
			entities: [{ value: "ent-a", label: "Entity A" }],
		});

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Acme" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Configure" }));
		expect(onOpenConfigDialog).toHaveBeenCalledWith("org-1");
	});

	it("marks rows with non-default config inside the actions menu", async () => {
		const { user } = renderTab({
			configSchema: [{ key: "apiBaseUrl", type: "string" }],
			configDefaults: { apiBaseUrl: "https://default.example" },
			orgsWithMappings: [
				{
					...orgs[0],
					mapping: {
						...orgs[0].mapping,
						config: { apiBaseUrl: "https://override.example" },
					} as OrgWithMapping["mapping"],
				},
			],
			entities: [{ value: "ent-a", label: "Entity A" }],
		});

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Acme" }),
		);
		expect(
			screen.getByLabelText("Custom configuration"),
		).toBeInTheDocument();
	});

	it("keeps Unlink disabled when there is no mapping to unlink", async () => {
		const { user } = renderTab({
			orgsWithMappings: orgs,
			entities: [{ value: "ent-a", label: "Entity A" }],
		});

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Beta" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Unlink" }),
		).toHaveAttribute("aria-disabled", "true");
	});
});

describe("IntegrationMappingsTab — no data provider manual input", () => {
	it("explains that auto-match needs a data provider while keeping Configure available", async () => {
		const { user } = renderTab({
			hasDataProvider: false,
			configSchema: [{ key: "apiBaseUrl", type: "string" }],
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Acme",
					mapping: undefined,
					formData: {
						organization_id: "org-1",
						entity_id: "",
						entity_name: "",
						config: {},
					},
				},
			],
		});

		expect(
			screen.queryByText(/auto-match unmapped/i),
		).not.toBeInTheDocument();
		expect(
			screen.getByText(/auto-match is unavailable/i),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Acme" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Configure" }),
		).toBeInTheDocument();
	});

	it("shows entity_id text input when hasDataProvider is false", () => {
		renderTab({
			hasDataProvider: false,
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Acme",
					mapping: undefined,
					formData: {
						organization_id: "org-1",
						entity_id: "",
						entity_name: "",
						config: {},
					},
				},
			],
		});
		expect(screen.getByPlaceholderText(/entity id/i)).toBeInTheDocument();
	});

	it("manual entity_id input does not call onUpdateOrgMapping on each keystroke (saves on blur)", async () => {
		const onUpdateOrgMapping = vi.fn();
		const { user } = renderTab({
			hasDataProvider: false,
			onUpdateOrgMapping,
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Org 1",
					formData: {
						organization_id: "org-1",
						entity_id: "",
						entity_name: "",
						config: {},
					},
				},
			],
		});
		const input = screen.getByPlaceholderText(/entity id/i);
		await user.type(input, "abc");
		// Typing should NOT trigger save
		expect(onUpdateOrgMapping).not.toHaveBeenCalled();
		// Blur triggers exactly one save with the final value
		await user.tab();
		expect(onUpdateOrgMapping).toHaveBeenCalledTimes(1);
		expect(onUpdateOrgMapping).toHaveBeenCalledWith("org-1", "abc", "abc");
	});
});

describe("IntegrationMappingsTab — OAuth connection column", () => {
	it("shows status only, with Connect offered in the row actions menu", async () => {
		const props = {
			hasOAuth: true,
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Org 1",
					mapping: {
						id: "m-1",
						oauth_token_id: null,
						connection_status: null,
					} as unknown as IntegrationMapping,
					formData: {
						organization_id: "org-1",
						entity_id: "",
						entity_name: "",
						config: {},
					},
				},
			],
		};
		const { user } = renderTab(props);
		expect(
			screen.getByText("Not connected", { exact: true }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /^connect/i }),
		).not.toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Org 1" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Connect" }),
		).toBeInTheDocument();
	});

	it("renders status badge from connection_status when mapping has a token", () => {
		const props = {
			hasOAuth: true,
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Org 1",
					mapping: {
						id: "m-1",
						oauth_token_id: "tok-1",
						connection_status: "completed",
					} as unknown as IntegrationMapping,
					formData: {
						organization_id: "org-1",
						entity_id: "x",
						entity_name: "X",
						config: {},
					},
				},
			],
		};
		renderTab(props);
		expect(
			screen.getByText("Connected", { exact: true }),
		).toBeInTheDocument();
	});

	it("renders Expired instead of Connected when a completed token is past expiry", async () => {
		const { user } = renderTab({
			hasOAuth: true,
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Org 1",
					mapping: {
						id: "m-1",
						oauth_token_id: "tok-1",
						connection_status: "completed",
						connection_expires_at: new Date(
							Date.now() - 60 * 60 * 1000,
						).toISOString(),
					} as unknown as IntegrationMapping,
					formData: {
						organization_id: "org-1",
						entity_id: "x",
						entity_name: "X",
						config: {},
					},
				},
			],
		});

		expect(
			screen.queryByText("Connected", { exact: true }),
		).not.toBeInTheDocument();
		expect(
			screen.getByText("Expired", { exact: true }),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Org 1" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Reconnect" }),
		).toBeInTheDocument();
	});

	it("Connected status offers refresh in the actions menu", async () => {
		const onRefreshMapping = vi.fn();
		const props = {
			hasOAuth: true,
			onRefreshMapping,
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Org 1",
					mapping: {
						id: "m-1",
						oauth_token_id: "tok-1",
						connection_status: "completed",
						connection_expires_at: new Date(
							Date.now() + 60 * 60 * 1000,
						).toISOString(),
					} as unknown as IntegrationMapping,
					formData: {
						organization_id: "org-1",
						entity_id: "x",
						entity_name: "X",
						config: {},
					},
				},
			],
		};
		const { user } = renderTab(props);
		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Org 1" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Refresh token" }));
		expect(onRefreshMapping).toHaveBeenCalledWith(
			props.orgsWithMappings[0],
		);
	});

	it("locks the row while a connect action is pending", async () => {
		const { user } = renderTab({
			hasOAuth: true,
			pendingMappingAction: { orgId: "org-1", action: "connect" },
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Org 1",
					mapping: {
						id: "m-1",
						oauth_token_id: "tok-1",
						connection_status: "failed",
						connection_message: "Temporary failure",
					} as unknown as IntegrationMapping,
					formData: {
						organization_id: "org-1",
						entity_id: "x",
						entity_name: "X",
						config: {},
					},
				},
			],
		});

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Org 1" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Retrying…" }),
		).toHaveAttribute("aria-disabled", "true");
		expect(
			screen.getByRole("menuitem", { name: "Configure" }),
		).toHaveAttribute("aria-disabled", "true");
		expect(
			screen.getByRole("menuitem", { name: "Disconnect" }),
		).toHaveAttribute("aria-disabled", "true");
		expect(
			screen.getByRole("menuitem", { name: "Unlink" }),
		).toHaveAttribute("aria-disabled", "true");
	});

	it("calls onConnectMapping with the org from the actions menu", async () => {
		const onConnectMapping = vi.fn();
		const org = {
			id: "org-1",
			name: "Org 1",
			mapping: {
				id: "m-1",
				oauth_token_id: null,
				connection_status: null,
			} as unknown as IntegrationMapping,
			formData: {
				organization_id: "org-1",
				entity_id: "",
				entity_name: "",
				config: {},
			},
		};
		const props = {
			hasOAuth: true,
			onConnectMapping,
			orgsWithMappings: [org],
		};
		const { user } = renderTab(props);
		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Org 1" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Connect" }));
		expect(onConnectMapping).toHaveBeenCalledWith(org);
	});

	it("offers Connect (not 'Save row first') when no mapping exists yet", async () => {
		const props = {
			hasOAuth: true,
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Org 1",
					mapping: undefined,
					formData: {
						organization_id: "org-1",
						entity_id: "",
						entity_name: "",
						config: {},
					},
				},
			],
		};
		const { user } = renderTab(props);
		expect(screen.queryByText(/save row first/i)).not.toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Org 1" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Connect" }),
		).toBeInTheDocument();
	});

	it("disconnects the selected mapping from its actions menu", async () => {
		const onDisconnectMapping = vi.fn();
		const props = {
			hasOAuth: true,
			onDisconnectMapping,
			orgsWithMappings: [
				{
					id: "org-1",
					name: "Org 1",
					mapping: {
						id: "m-1",
						oauth_token_id: "tok-1",
						connection_status: "completed",
					} as unknown as IntegrationMapping,
					formData: {
						organization_id: "org-1",
						entity_id: "x",
						entity_name: "X",
						config: {},
					},
				},
			],
		};
		const { user } = renderTab(props);
		await user.click(
			screen.getByRole("button", { name: "Mapping actions for Org 1" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Disconnect" }));
		expect(onDisconnectMapping).toHaveBeenCalledWith(
			props.orgsWithMappings[0],
		);
	});
});
