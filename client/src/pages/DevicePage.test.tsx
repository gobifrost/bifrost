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
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
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

vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Example Portal",
}));

import { DevicePage } from "./DevicePage";

beforeEach(() => {
	mockLogout.mockReset();
	mockNavigate.mockReset();
	authState.isAuthenticated = true;
	localStorage.clear();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("DevicePage", () => {
	it("redirects an unauthenticated visitor back through login", async () => {
		authState.isAuthenticated = false;
		renderWithProviders(<DevicePage />, { initialEntries: ["/device"] });
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/login", {
				state: { from: "/device" },
				replace: true,
			}),
		);
	});

	it("returns expired authorization sessions to login", async () => {
		localStorage.setItem("bifrost_access_token", "access-token");
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => ({ ok: false, status: 401 })),
		);
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device"],
		});
		await user.type(screen.getByLabelText(/device code/i), "abcd1234");
		await user.click(
			screen.getByRole("button", { name: /authorize device/i }),
		);
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/login", {
				state: { from: "/device" },
				replace: true,
			}),
		);
	});

	it("lets an authenticated user leave the device-code form", async () => {
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device"],
		});

		expect(screen.getByText(/authorizing as/i)).toBeInTheDocument();
		expect(screen.getByAltText("Example Portal")).toBeInTheDocument();

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
			expect(
				screen.getByRole("heading", { name: "CLI Authorized!" }),
			).toHaveFocus();
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
