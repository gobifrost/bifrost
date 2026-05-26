import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { initOAuth } = vi.hoisted(() => ({
	initOAuth: vi.fn(),
}));

import { Login } from "./Login";

vi.mock("@/services/auth", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/auth")>(
			"@/services/auth",
		);
	return {
		...actual,
		getOAuthProviders: vi.fn(async () => [
			{ name: "microsoft", display_name: "Microsoft", icon: "microsoft" },
		]),
		initOAuth,
	};
});

vi.mock("@/services/passkeys", () => ({
	supportsPasskeys: () => false,
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		login: vi.fn(),
		loginWithMfa: vi.fn(),
		loginWithPasskey: vi.fn(),
		isAuthenticated: false,
		isLoading: false,
	}),
}));

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <div aria-label="Bifrost" />,
}));

describe("Login OAuth flow", () => {
	const originalAssign = window.location.assign;

	beforeEach(() => {
		initOAuth.mockResolvedValue({
			authorization_url: "https://login.example.test/authorize",
			state: "server-state",
		});
		vi.spyOn(window.location, "assign").mockImplementation(() => {});
		sessionStorage.clear();
	});

	afterEach(() => {
		vi.restoreAllMocks();
		window.location.assign = originalAssign;
	});

	it("redirects to the provider without storing OAuth state in session storage", async () => {
		const setItem = vi.spyOn(Storage.prototype, "setItem");

		render(
			<MemoryRouter>
				<Login />
			</MemoryRouter>,
		);

		await userEvent.click(
			await screen.findByRole("button", { name: /microsoft/i }),
		);

		await waitFor(() => {
			expect(initOAuth).toHaveBeenCalledWith(
				"microsoft",
				"http://localhost:3000/auth/callback/microsoft",
			);
		});
		expect(window.location.assign).toHaveBeenCalledWith(
			"https://login.example.test/authorize",
		);
		expect(setItem).not.toHaveBeenCalledWith(
			"oauth_provider",
			expect.any(String),
		);
		expect(setItem).not.toHaveBeenCalledWith(
			"oauth_state",
			expect.any(String),
		);
	});
});
