import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		isPlatformAdmin: true,
		canAt: () => true,
	}),
}));

const rename = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
vi.mock("@/services/identities", () => ({
	useRenameIdentity: () => ({ ...rename, isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

import { IdentityProfileForm } from "./IdentityProfileForm";

function identity(overrides: Partial<User>): User {
	return {
		id: "identity-1",
		email: "identity-1@identities.bifrost.internal",
		name: "Default Identity",
		identity_kind: "org_default",
		organization_id: "org-1",
		is_protected: false,
		...overrides,
	} as User;
}

beforeEach(() => {
	rename.mutateAsync.mockReset();
});

describe("IdentityProfileForm", () => {
	it.each(["org_default", "global_default"])(
		"keeps a %s identity's name read-only",
		(kind) => {
			renderWithProviders(
				<IdentityProfileForm
					identity={identity({ identity_kind: kind })}
				/>,
			);

			const name = screen.getByRole("textbox", { name: "Name" });
			expect(name).toHaveValue("Default Identity");
			expect(name).toHaveAttribute("readonly");
			expect(name).toHaveAccessibleDescription(
				"Default identities keep their name.",
			);
			expect(
				screen.queryByRole("button", { name: "Save Name" }),
			).not.toBeInTheDocument();
		},
	);

	it("shows why a custom identity can't take a name its organization already has", async () => {
		rename.mutateAsync.mockRejectedValue({
			detail: 'An identity named "Backup Runner" already exists in Contoso',
		});
		const { user } = renderWithProviders(
			<IdentityProfileForm
				identity={identity({
					name: "Nightly Sync",
					identity_kind: "custom",
				})}
			/>,
		);

		const name = screen.getByRole("textbox", { name: "Name" });
		await user.clear(name);
		await user.type(name, "Backup Runner");
		await user.click(screen.getByRole("button", { name: "Save Name" }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			'An identity named "Backup Runner" already exists in Contoso',
		);
	});
});
