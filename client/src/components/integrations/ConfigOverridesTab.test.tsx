/**
 * Component tests for ConfigOverridesTab.
 *
 * The tab is driven by orgsWithMappings + configSchema and dispatches edits
 * through useUpdateMapping. We mock that hook at the module level so we can
 * assert the save payload without booting the backend.
 *
 * Behaviors covered:
 *   - empty states (no schema, no overrides)
 *   - rows are only rendered for orgs with explicit overrides
 *   - clicking a cell opens the inline editor, Enter saves the new value
 *   - secret fields are filtered out entirely (must never be displayed)
 *   - delete confirmation dispatches a save with the override key set to null
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, fireEvent, waitFor } from "@/test-utils";
import type { ConfigSchemaItem } from "@/services/integrations";

const mockMutateAsync = vi.fn();

vi.mock("@/services/integrations", async () => {
	const actual = await vi.importActual<
		typeof import("@/services/integrations")
	>("@/services/integrations");
	return {
		...actual,
		useUpdateMapping: () => ({
			mutateAsync: mockMutateAsync,
			isPending: false,
		}),
	};
});

import { ConfigOverridesTab } from "./ConfigOverridesTab";

type OrgWithMapping = Parameters<
	typeof ConfigOverridesTab
>[0]["orgsWithMappings"][number];

function makeSchema(): ConfigSchemaItem[] {
	return [
		{ key: "tenant_id", type: "string", required: false },
		{ key: "api_secret", type: "secret", required: false },
	] as ConfigSchemaItem[];
}

function makeOrg(overrides: Partial<OrgWithMapping> = {}): OrgWithMapping {
	return {
		id: "org-1",
		name: "Acme",
		mapping: {
			id: "map-1",
			integration_id: "int-1",
			organization_id: "org-1",
			entity_id: "ent-1",
			entity_name: "Entity 1",
			oauth_token_id: null,
			config: { tenant_id: "acme-tenant" },
		} as unknown as OrgWithMapping["mapping"],
		formData: {
			organization_id: "org-1",
			entity_id: "ent-1",
			entity_name: "Entity 1",
			config: { tenant_id: "acme-tenant" },
		},
		...overrides,
	};
}

beforeEach(() => {
	mockMutateAsync.mockReset();
	mockMutateAsync.mockResolvedValue(undefined);
});

describe("ConfigOverridesTab — empty states", () => {
	it("shows 'No configuration schema' when schema is empty", () => {
		renderWithProviders(
			<ConfigOverridesTab
				orgsWithMappings={[makeOrg()]}
				configSchema={[]}
				integrationId="int-1"
				canManageMapping={() => true}
			/>,
		);
		expect(
			screen.getByText(/no configuration schema/i),
		).toBeInTheDocument();
	});

	it("shows 'No configuration overrides' when no org has an override", () => {
		renderWithProviders(
			<ConfigOverridesTab
				orgsWithMappings={[
					makeOrg({
						mapping: {
							...makeOrg().mapping!,
							config: {},
						} as OrgWithMapping["mapping"],
					}),
				]}
				configSchema={makeSchema()}
				integrationId="int-1"
				canManageMapping={() => true}
			/>,
		);
		expect(
			screen.getByText(/no configuration overrides/i),
		).toBeInTheDocument();
	});
});

describe("ConfigOverridesTab — rendering", () => {
	it("renders one row per org+override and excludes secret fields", () => {
		renderWithProviders(
			<ConfigOverridesTab
				orgsWithMappings={[makeOrg()]}
				configSchema={makeSchema()}
				integrationId="int-1"
				canManageMapping={() => true}
			/>,
		);
		expect(screen.getByText("Acme")).toBeInTheDocument();
		expect(screen.getByText("tenant_id")).toBeInTheDocument();
		// api_secret should not appear at all — never show secret overrides
		expect(screen.queryByText("api_secret")).not.toBeInTheDocument();
	});
});

describe("ConfigOverridesTab — inline edit save", () => {
	it("Enter in the editor dispatches the update with the merged config", async () => {
		renderWithProviders(
			<ConfigOverridesTab
				orgsWithMappings={[makeOrg()]}
				configSchema={makeSchema()}
				integrationId="int-1"
				canManageMapping={() => true}
			/>,
		);

		// Click the value cell to enter edit mode
		await waitFor(() =>
			expect(screen.getByText("acme-tenant")).toBeInTheDocument(),
		);
		const cell = screen.getByText("acme-tenant");
		fireEvent.click(cell);

		// Now an input is rendered with the current value.
		const input = (await screen.findByDisplayValue(
			"acme-tenant",
		)) as HTMLInputElement;
		fireEvent.change(input, { target: { value: "new-tenant" } });
		fireEvent.keyDown(input, { key: "Enter" });

		await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledTimes(1));
		const payload = mockMutateAsync.mock.calls[0]![0];
		expect(payload.params.path).toEqual({
			integration_id: "int-1",
			mapping_id: "map-1",
		});
		expect(payload.body.config).toEqual({ tenant_id: "new-tenant" });
	});
});

describe("ConfigOverridesTab — delete flow", () => {
	it("delete dispatches an update setting the key to null (backend tombstone)", async () => {
		const { user } = renderWithProviders(
			<ConfigOverridesTab
				orgsWithMappings={[makeOrg()]}
				configSchema={makeSchema()}
				integrationId="int-1"
				canManageMapping={() => true}
			/>,
		);

		await user.click(
			screen.getByRole("button", {
				name: "More actions for Acme tenant_id",
			}),
		);

		await user.click(
			screen.getByRole("menuitem", { name: "Delete override" }),
		);

		// Confirm the destructive action
		await user.click(screen.getByRole("button", { name: /^delete$/i }));

		await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledTimes(1));
		const payload = mockMutateAsync.mock.calls[0]![0];
		expect(payload.body.config).toEqual({ tenant_id: null });
	});
});
