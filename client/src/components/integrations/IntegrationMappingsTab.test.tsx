/**
 * Component tests for IntegrationMappingsTab.
 *
 * Covers the three user-visible states: no data provider, no orgs, and the
 * populated table — plus the Delete and Edit callbacks. EntitySelector +
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

	it("fires onDeleteMapping for the org whose Unlink button is clicked", async () => {
		const { user, onDeleteMapping } = renderTab({
			orgsWithMappings: orgs,
			entities: [{ value: "ent-a", label: "Entity A" }],
		});

		// Unlink title is "Unlink mapping" for mapped orgs. Find it via title.
		const unlinkButtons = screen
			.getAllByRole("button")
			.filter((b) => b.getAttribute("title") === "Unlink mapping");
		expect(unlinkButtons).toHaveLength(1);
		await user.click(unlinkButtons[0]);
		expect(onDeleteMapping).toHaveBeenCalledWith(orgs[0]);
	});

	it("opens the config dialog for the clicked row", async () => {
		const { user, onOpenConfigDialog } = renderTab({
			orgsWithMappings: orgs,
			entities: [{ value: "ent-a", label: "Entity A" }],
		});

		const configureBtn = screen
			.getAllByRole("button")
			.find((b) => b.getAttribute("title") === "Configure");
		expect(configureBtn).toBeDefined();
		await user.click(configureBtn!);
		expect(onOpenConfigDialog).toHaveBeenCalledWith("org-1");
	});

	it("keeps the Unlink button disabled when there is no mapping to unlink", () => {
		renderTab({
			orgsWithMappings: orgs,
			entities: [{ value: "ent-a", label: "Entity A" }],
		});

		const row = screen.getByText("Beta").closest("tr")!;
		const unlinkBtn = within(row)
			.getAllByRole("button")
			.find((b) => b.getAttribute("title") === "No mapping to unlink");
		expect(unlinkBtn).toBeDefined();
		expect(unlinkBtn).toBeDisabled();
	});
});

describe("IntegrationMappingsTab — no data provider manual input", () => {
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
	it("renders Connect button when integration has OAuth and mapping has no token", () => {
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
		renderTab(props);
		expect(screen.getByRole("button", { name: /connect/i })).toBeInTheDocument();
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
		expect(screen.getByText(/connected/i)).toBeInTheDocument();
	});

	it("Connected badge has an inline refresh button that calls onRefreshMapping", async () => {
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
		const refreshBtn = screen.getByRole("button", { name: /refresh token/i });
		await user.click(refreshBtn);
		expect(onRefreshMapping).toHaveBeenCalledWith("m-1");
	});

	it("calls onConnectMapping with the org when Connect button is clicked", async () => {
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
		await user.click(screen.getByRole("button", { name: /connect/i }));
		expect(onConnectMapping).toHaveBeenCalledWith(org);
	});

	it("shows Connect button (not 'Save row first') when no mapping exists yet", () => {
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
		renderTab(props);
		expect(screen.getByRole("button", { name: /connect/i })).toBeInTheDocument();
		expect(screen.queryByText(/save row first/i)).not.toBeInTheDocument();
	});

	it("renders Disconnect button when mapping has an oauth_token_id", () => {
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
		renderTab(props);
		expect(screen.getByTitle(/disconnect oauth/i)).toBeInTheDocument();
	});
});
