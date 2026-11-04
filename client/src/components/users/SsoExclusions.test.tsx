import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import {
	SsoExclusions,
	UserSsoExclusion,
	OrganizationSsoExclusion,
} from "./SsoExclusions";
import type { components } from "@/lib/v1";

const fixture = vi.hoisted(() => ({
	preference: {
		password_login_enabled: false,
		passkey_login_enabled: false,
		auto_redirect_to_sso: false,
		default_sso_provider: null,
		excluded_user_ids: [] as string[],
		excluded_organization_ids: [] as string[],
	},
	update: vi.fn(),
}));
const person = {
	id: "person",
	name: "Example Person",
	email: "example@example.test",
	is_superuser: false,
	is_active: true,
} as components["schemas"]["UserPublic"];
vi.mock("@/services/oauth-config", () => ({
	useOAuthConfigs: () => ({ data: { login_preference: fixture.preference } }),
	useUpdateOAuthLoginPreference: () => ({
		mutateAsync: fixture.update,
		isPending: false,
	}),
}));
vi.mock("@/hooks/useUsers", () => ({
	useUsersFiltered: () => ({ data: [person] }),
}));
vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [{ id: "org", name: "Example Organization" }],
	}),
}));

describe("SSO exclusions", () => {
	beforeEach(() => {
		fixture.preference.excluded_user_ids = [];
		fixture.preference.excluded_organization_ids = [];
		fixture.update.mockReset();
		fixture.update.mockImplementation(async ({ body }) => {
			fixture.preference = body;
		});
	});
	it("settings and user screens write and read the same exclusion", async () => {
		const settings = renderWithProviders(<SsoExclusions />);
		await settings.user.click(
			screen.getByRole("combobox", { name: "Excluded Users" }),
		);
		await settings.user.type(
			screen.getByPlaceholderText("Search users…"),
			"example",
		);
		await settings.user.click(
			screen.getByRole("option", { name: /Example Person/ }),
		);
		await settings.user.keyboard("{Escape}");
		expect(fixture.update).not.toHaveBeenCalled();
		await settings.user.click(
			screen.getByRole("button", { name: "Save Exclusions" }),
		);
		await waitFor(() =>
			expect(fixture.update).toHaveBeenCalledWith({
				body: expect.objectContaining({
					excluded_user_ids: [person.id],
				}),
			}),
		);
		settings.unmount();
		const detail = renderWithProviders(<UserSsoExclusion user={person} />);
		expect(screen.getByRole("switch")).toHaveAttribute(
			"aria-checked",
			"true",
		);
		await detail.user.click(screen.getByRole("switch"));
		await waitFor(() =>
			expect(fixture.preference.excluded_user_ids).toEqual([]),
		);
	});
	it("organization enforcement preserves individual exclusions", async () => {
		fixture.preference.excluded_user_ids = ["recovery-admin"];
		const { user } = renderWithProviders(<SsoExclusions />);
		await user.click(
			screen.getByRole("combobox", { name: "Excluded Organizations" }),
		);
		await user.click(
			screen.getByRole("option", { name: "Example Organization" }),
		);
		await user.keyboard("{Escape}");
		await user.click(
			screen.getByRole("button", { name: "Save Exclusions" }),
		);
		await waitFor(() =>
			expect(fixture.update).toHaveBeenCalledWith({
				body: expect.objectContaining({
					excluded_user_ids: ["recovery-admin"],
					excluded_organization_ids: ["org"],
				}),
			}),
		);
	});
	it("shows a refused last-admin exclusion change", async () => {
		fixture.update.mockRejectedValue(
			new Error(
				"At least one active platform admin must remain excluded",
			),
		);
		const { user } = renderWithProviders(
			<UserSsoExclusion user={person} />,
		);
		await user.click(screen.getByRole("switch"));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"active platform admin",
		);
	});
	it("organization detail uses the same exclusion wording and selected meaning", async () => {
		fixture.preference.excluded_user_ids = ["recovery-admin"];
		fixture.preference.excluded_organization_ids = ["org"];
		const { user } = renderWithProviders(
			<OrganizationSsoExclusion organizationId="org" />,
		);
		expect(
			screen.getByRole("switch", {
				name: "Exclude From SSO Enforcement",
			}),
		).toBeChecked();
		await user.click(screen.getByRole("switch"));
		await waitFor(() =>
			expect(fixture.update).toHaveBeenCalledWith({
				body: expect.objectContaining({
					excluded_user_ids: ["recovery-admin"],
					excluded_organization_ids: [],
				}),
			}),
		);
	});
	it("preserves the selection draft when the recovery guard refuses a save", async () => {
		fixture.update.mockRejectedValue(
			new Error(
				"At least one active platform admin must remain excluded",
			),
		);
		const { user } = renderWithProviders(<SsoExclusions />);
		await user.click(
			screen.getByRole("combobox", { name: "Excluded Users" }),
		);
		await user.click(
			screen.getByRole("option", { name: /Example Person/ }),
		);
		await user.keyboard("{Escape}");
		await user.click(
			screen.getByRole("button", { name: "Save Exclusions" }),
		);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"active platform admin",
		);
		expect(
			screen.getByRole("combobox", { name: "Excluded Users" }),
		).toHaveTextContent("1 selected");
	});
	it("turning on the organization exclusion adds it without changing user exclusions", async () => {
		fixture.preference.excluded_user_ids = ["recovery-admin"];
		const { user } = renderWithProviders(
			<OrganizationSsoExclusion organizationId="org" />,
		);
		const toggle = screen.getByRole("switch", {
			name: "Exclude From SSO Enforcement",
		});
		expect(toggle).not.toBeChecked();
		expect(
			screen.getByText(
				"Excluded accounts can use password and passkey sign-in. Changes apply immediately.",
			),
		).toBeVisible();
		await user.click(toggle);
		await waitFor(() =>
			expect(fixture.update).toHaveBeenCalledWith({
				body: expect.objectContaining({
					excluded_user_ids: ["recovery-admin"],
					excluded_organization_ids: ["org"],
				}),
			}),
		);
	});
});
