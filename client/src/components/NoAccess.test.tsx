import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mockLogout = vi.fn();
const mockNavigate = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		logout: mockLogout,
	}),
}));

vi.mock("react-router-dom", async () => {
	const actual = await vi.importActual<typeof import("react-router-dom")>(
		"react-router-dom",
	);
	return { ...actual, useNavigate: () => mockNavigate };
});

import { NoAccess } from "./NoAccess";

beforeEach(() => {
	mockLogout.mockReset();
	mockNavigate.mockReset();
});

describe("NoAccess", () => {
	it("lets the user return to the dashboard", async () => {
		const { user } = renderWithProviders(<NoAccess />);

		await user.click(
			screen.getByRole("button", { name: /return to dashboard/i }),
		);

		expect(mockNavigate).toHaveBeenCalledWith("/");
	});

	it("keeps sign out available", async () => {
		const { user } = renderWithProviders(<NoAccess />);

		await user.click(screen.getByRole("button", { name: /sign out/i }));

		expect(mockLogout).toHaveBeenCalledTimes(1);
	});
});
