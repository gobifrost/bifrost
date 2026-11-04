import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { SsoExclusions, UserSsoExclusion } from "./SsoExclusions";
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
} as components["schemas"]["UserPublic"];
vi.mock("@/services/oauth-config", () => ({
	useOAuthConfigs: () => ({ data: { login_preference: fixture.preference } }),
	useUpdateOAuthLoginPreference: () => ({
		mutateAsync: fixture.update,
		isPending: false,
	}),
}));
vi.mock("@/hooks/useUsers", () => ({
	useUsersPage: () => ({ data: { items: [person], total: 1 } }),
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
			screen.getByRole("switch", {
				name: "Exclude From SSO Enforcement",
			}),
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
			screen.getByRole("switch", { name: "Example Organization" }),
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
});
