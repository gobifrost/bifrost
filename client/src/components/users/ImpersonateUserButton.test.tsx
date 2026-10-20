import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import type { components } from "@/lib/v1";

const auth = vi.hoisted(() => ({
	user: { id: "operator" },
	isImpersonating: false,
	startImpersonation: vi.fn(),
}));
const canAt = vi.hoisted(() => vi.fn());

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => auth,
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ canAt }),
}));

import { ImpersonateUserButton } from "./ImpersonateUserButton";

const target = {
	id: "target",
	email: "avery@example.test",
	name: "Avery Example",
	is_active: true,
	is_system: false,
	is_protected: false,
	organization_id: "target-org",
} as components["schemas"]["UserPublic"];

describe("ImpersonateUserButton", () => {
	it("does not render the action without the impersonation permission", () => {
		canAt.mockReset().mockReturnValue(false);
		renderWithProviders(<ImpersonateUserButton user={target} />);
		expect(screen.queryByRole("button", { name: "Impersonate" })).toBeNull();
	});

	it("also requires privileged access for a protected target", () => {
		canAt.mockReset().mockImplementation((permission: string) => permission === "users.impersonate");
		renderWithProviders(<ImpersonateUserButton user={{ ...target, is_protected: true }} />);
		expect(screen.queryByRole("button", { name: "Impersonate" })).toBeNull();
	});

	it("uses the existing impersonation permission at the target organization", async () => {
		auth.startImpersonation.mockReset().mockResolvedValue(undefined);
		canAt.mockReset().mockReturnValue(true);
		const { user } = renderWithProviders(<ImpersonateUserButton user={target} />);

		await user.click(screen.getByRole("button", { name: "Impersonate" }));

		expect(canAt).toHaveBeenCalledWith("users.impersonate", {
			kind: "org",
			id: "target-org",
		});
		expect(auth.startImpersonation).toHaveBeenCalledWith({
			id: "target",
			email: "avery@example.test",
			name: "Avery Example",
		});
	});

	it("does not offer impersonation for a managed identity", () => {
		canAt.mockReset().mockReturnValue(true);
		renderWithProviders(
			<ImpersonateUserButton
				user={{ ...target, identity_kind: "org_default" }}
			/>,
		);

		expect(screen.queryByRole("button", { name: "Impersonate" })).toBeNull();
	});
});
