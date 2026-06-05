import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { PasskeySetupBadge } from "./PasskeySetupBadge";

const mockUsePasskeyList = vi.fn();

vi.mock("@/hooks/usePasskeys", () => ({
	usePasskeyList: () => mockUsePasskeyList(),
}));

vi.mock("@/services/passkeys", () => ({
	supportsPasskeys: () => true,
}));

beforeEach(() => {
	localStorage.clear();
	mockUsePasskeyList.mockReturnValue({
		data: { count: 0, passkeys: [] },
		isLoading: false,
	});
});

describe("PasskeySetupBadge", () => {
	it("renders a compact header action for users who can add a passkey", () => {
		const { container } = renderWithProviders(<PasskeySetupBadge />);

		expect(
			screen.getByRole("button", { name: /set up passkey/i }),
		).toBeInTheDocument();
		expect(
			container.querySelector("[data-slot='passkey-setup-indicator']"),
		).toHaveClass("bg-primary");
		expect(
			screen.queryByText(/add a passkey to sign in faster/i),
		).not.toBeInTheDocument();
	});

	it("stays hidden when the user already has a passkey", () => {
		mockUsePasskeyList.mockReturnValue({
			data: { count: 1, passkeys: [{ id: "passkey-1" }] },
			isLoading: false,
		});

		renderWithProviders(<PasskeySetupBadge />);

		expect(
			screen.queryByRole("button", { name: /set up passkey/i }),
		).not.toBeInTheDocument();
	});
});
