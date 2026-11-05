import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { setImpersonationToken } from "@/lib/auth-token";

const mockLogout = vi.fn();
const mockNavigate = vi.fn();
const authState = {
	isAuthenticated: true,
	isLoading: false,
	logout: mockLogout,
	user: { email: "dev@gobifrost.com" },
};

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => authState }));
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return { ...actual, useNavigate: () => mockNavigate };
});
vi.mock("@/components/branding/Logo", () => ({
	Logo: ({ alt = "Bifrost" }: { alt?: string }) => <img alt={alt} />,
}));
vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Example Portal",
}));

import { DevicePage } from "./DevicePage";

function successfulResponse() {
	return { ok: true, status: 200, json: async () => ({}) } as Response;
}

beforeEach(() => {
	mockLogout.mockReset();
	mockNavigate.mockReset();
	authState.isAuthenticated = true;
	authState.isLoading = false;
	localStorage.clear();
	sessionStorage.clear();
});

afterEach(() => vi.unstubAllGlobals());

describe("DevicePage", () => {
	it("requires an explicit confirmation after entering a code", async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device"],
		});

		expect(
			screen.getByRole("heading", { name: "Bifrost CLI" }),
		).toBeInTheDocument();
		await user.type(screen.getByLabelText(/device code/i), "abcd1234");
		await user.click(screen.getByRole("button", { name: "Continue" }));

		expect(
			screen.getByRole("heading", { name: "Connect Bifrost CLI" }),
		).toBeInTheDocument();
		expect(fetchMock).not.toHaveBeenCalled();
		expect(mockNavigate).toHaveBeenCalledWith(
			"/device?user_code=ABCD-1234",
			{ replace: true },
		);
	});

	it("opens a valid query code in confirmation without authorizing", () => {
		const fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
		renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=abcd1234"],
		});

		expect(
			screen.getByRole("heading", { name: "Connect Bifrost CLI" }),
		).toBeInTheDocument();
		expect(
			screen.getByLabelText("Device code ABCD-1234"),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: "Connect Bifrost CLI" }),
		).toHaveFocus();
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it("does not truncate a malformed query into a confirmation", () => {
		renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=ABCDEFGHI"],
		});

		expect(
			screen.getByRole("heading", { name: "Bifrost CLI" }),
		).toBeInTheDocument();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"The code in this link is invalid",
		);
		expect(screen.getByLabelText(/device code/i)).toHaveValue("");
	});

	it("approves with the active impersonation token only after Connect CLI", async () => {
		localStorage.setItem("bifrost_access_token", "operator-token");
		setImpersonationToken("impersonated-token");
		const fetchMock = vi.fn(async () => successfulResponse());
		vi.stubGlobal("fetch", fetchMock);
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=ABCD-EFGH"],
		});

		await user.click(screen.getByRole("button", { name: "Connect CLI" }));

		await waitFor(() =>
			expect(
				screen.getByRole("heading", { name: "CLI Connected" }),
			).toHaveFocus(),
		);
		expect(fetchMock).toHaveBeenCalledWith(
			"/auth/device/authorize",
			expect.objectContaining({
				headers: expect.objectContaining({
					Authorization: "Bearer impersonated-token",
				}),
				body: JSON.stringify({
					user_code: "ABCD-EFGH",
				}),
			}),
		);
	});

	it("denies explicitly and tells the person what happened in the terminal", async () => {
		localStorage.setItem("bifrost_access_token", "access-token");
		const fetchMock = vi.fn(async () => successfulResponse());
		vi.stubGlobal("fetch", fetchMock);
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=ABCD-EFGH"],
		});

		await user.click(screen.getByRole("button", { name: "Deny" }));

		await waitFor(() =>
			expect(
				screen.getByRole("heading", { name: "CLI Connection Denied" }),
			).toHaveFocus(),
		);
		expect(fetchMock).toHaveBeenCalledWith(
			"/auth/device/deny",
			expect.objectContaining({
				body: JSON.stringify({
					user_code: "ABCD-EFGH",
				}),
			}),
		);
		expect(
			screen.getByText(/no CLI credentials were issued/i),
		).toBeInTheDocument();
	});

	it("never approves when an older API does not support denial", async () => {
		localStorage.setItem("bifrost_access_token", "access-token");
		const fetchMock = vi.fn(async () => ({ ok: false, status: 404 }));
		vi.stubGlobal("fetch", fetchMock);
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=ABCD-EFGH"],
		});
		await user.click(screen.getByRole("button", { name: "Deny" }));
		await screen.findByRole("heading", { name: "CLI Connection Failed" });
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock).toHaveBeenCalledWith(
			"/auth/device/deny",
			expect.objectContaining({
				body: JSON.stringify({ user_code: "ABCD-EFGH" }),
			}),
		);
	});

	it.each(["Connect CLI", "Deny"])(
		"prevents duplicate requests while %s is pending",
		async (action) => {
			localStorage.setItem("bifrost_access_token", "access-token");
			let resolveRequest: (response: Response) => void;
			const fetchMock = vi.fn(
				() =>
					new Promise<Response>((resolve) => {
						resolveRequest = resolve;
					}),
			);
			vi.stubGlobal("fetch", fetchMock);
			const { user } = renderWithProviders(<DevicePage />, {
				initialEntries: ["/device?user_code=ABCD-EFGH"],
			});

			const selected = screen.getByRole("button", {
				name: action,
			});
			const other = screen.getByRole("button", {
				name: action === "Deny" ? "Connect CLI" : "Deny",
			});
			await user.click(selected);
			expect(selected).toHaveAttribute("aria-busy", "true");
			expect(other).toHaveAttribute("aria-busy", "false");
			expect(other).toBeDisabled();
			await user.click(selected);
			expect(fetchMock).toHaveBeenCalledTimes(1);
			resolveRequest!(successfulResponse());
			await screen.findByRole("heading", {
				name:
					action === "Deny"
						? "CLI Connection Denied"
						: "CLI Connected",
			});
		},
	);

	it("preserves the complete device target when authentication expires", async () => {
		localStorage.setItem("bifrost_access_token", "access-token");
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => ({ ok: false, status: 401 })),
		);
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=ABCD-EFGH"],
		});

		await user.click(screen.getByRole("button", { name: "Connect CLI" }));

		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/login", {
				state: { from: "/device?user_code=ABCD-EFGH" },
				replace: true,
			}),
		);
	});

	it("hides the confirmation until shared authentication finishes", () => {
		authState.isAuthenticated = false;
		renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=ABCD-EFGH"],
		});
		expect(
			screen.queryByRole("button", { name: "Connect CLI" }),
		).not.toBeInTheDocument();
	});

	it("allows code editing, reset, dashboard return, and account switching", async () => {
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=ABCD-EFGH"],
		});

		await user.click(screen.getByRole("button", { name: "Edit Code" }));
		expect(screen.getByLabelText(/device code/i)).toHaveValue("ABCD-EFGH");
		expect(mockNavigate).toHaveBeenCalledWith("/device", {
			replace: true,
			state: { deviceCodeDraft: "ABCD-EFGH" },
		});

		await user.click(
			screen.getByRole("button", { name: /return to dashboard/i }),
		);
		expect(mockNavigate).toHaveBeenCalledWith("/");
		await user.click(screen.getByRole("button", { name: /sign out/i }));
		expect(mockLogout).toHaveBeenCalledTimes(1);
	});

	it("reports an expired code and resets without reopening stale query state", async () => {
		localStorage.setItem("bifrost_access_token", "access-token");
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => ({ ok: false, status: 404 })),
		);
		const { user } = renderWithProviders(<DevicePage />, {
			initialEntries: ["/device?user_code=ABCD-EFGH"],
		});

		await user.click(screen.getByRole("button", { name: "Connect CLI" }));
		await screen.findByRole("heading", { name: "CLI Connection Failed" });
		await user.click(
			screen.getByRole("button", { name: "Try Another Code" }),
		);

		expect(screen.getByLabelText(/device code/i)).toHaveValue("");
		expect(mockNavigate).toHaveBeenCalledWith("/device", { replace: true });
	});
});
