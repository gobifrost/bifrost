import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const setSelectedBoundary = vi.fn();
const useAuthorizationBoundary = vi.fn();

vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => useAuthorizationBoundary(),
}));

import { AuthorizationBoundaryPicker } from "./AuthorizationBoundaryPicker";

const organization = {
	boundary: "organization:customer-one",
	kind: "organization" as const,
	label: "Customer One",
	capabilities: ["builder.read"],
	organization_id: "customer-one",
	is_provider: false,
};
const platform = {
	boundary: "platform",
	kind: "platform" as const,
	label: "Global",
	capabilities: ["builder.execute"],
	is_provider: false,
};

describe("AuthorizationBoundaryPicker", () => {
	it("makes the active request context visible and selectable", async () => {
		useAuthorizationBoundary.mockReturnValue({
			targets: [organization, platform],
			selectedTarget: organization,
			selectedBoundary: organization.boundary,
			setSelectedBoundary,
		});
		const { user } = renderWithProviders(<AuthorizationBoundaryPicker />);

		await user.click(
			screen.getByRole("button", { name: "Working in Customer One" }),
		);
		expect(screen.getByText("Platform-wide resources")).toBeVisible();
		await user.click(screen.getByRole("menuitemradio", { name: /Global/ }));
		expect(setSelectedBoundary).toHaveBeenCalledWith("platform");
	});

	it("stays out of the way when there is only one possible context", () => {
		useAuthorizationBoundary.mockReturnValue({
			targets: [organization],
			selectedTarget: organization,
			selectedBoundary: organization.boundary,
			setSelectedBoundary,
		});
		renderWithProviders(<AuthorizationBoundaryPicker />);

		expect(
			screen.queryByRole("button", { name: /Working in/ }),
		).not.toBeInTheDocument();
	});
});
