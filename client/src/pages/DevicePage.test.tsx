import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockLogout = vi.fn();
const mockNavigate = vi.fn();

const authState = {
	isAuthenticated: true,
	isLoading: false,
	logout: mockLogout,
	user: { email: "dev@gobifrost.com" },
};

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => authState,
}));

vi.mock("react-router-dom", async () => {
	const actual = await vi.importActual<typeof import("react-router-dom")>(
		"react-router-dom",
	);
	return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

vi.mock("@/components/branding/Logo", () => ({
	Logo: ({ alt = "Bifrost" }: { alt?: string }) => <img alt={alt} />,
}));

import { DevicePage } from "./DevicePage";

beforeEach(() => {
	mockLogout.mockReset();
	mockNavigate.mockReset();
	localStorage.clear();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("DevicePage", () => {
	it("lets an authenticated user leave the device-code form", async () => {
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device"],
		});

		expect(screen.getByText(/authorizing as/i)).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: /return to dashboard/i }),
		);
		expect(mockNavigate).toHaveBeenCalledWith("/");

		await user.click(screen.getByRole("button", { name: /sign out/i }));
		expect(mockLogout).toHaveBeenCalledTimes(1);
	});

	it("shows dashboard and secondary actions after authorization", async () => {
		localStorage.setItem("bifrost_access_token", "access-token");
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => ({
				ok: true,
				status: 200,
				json: async () => ({}),
			})) as unknown as typeof fetch,
		);

		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device"],
		});

		await user.type(screen.getByLabelText(/device code/i), "abcd1234");
		await user.click(
			screen.getByRole("button", { name: /authorize device/i }),
		);

		await waitFor(() => {
			expect(screen.getByText(/cli authorized/i)).toBeInTheDocument();
		});

		expect(
			screen.getByRole("button", { name: /return to dashboard/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /authorize another device/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /sign out/i }),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: /return to dashboard/i }),
		);
		expect(mockNavigate).toHaveBeenCalledWith("/");
	});
});
