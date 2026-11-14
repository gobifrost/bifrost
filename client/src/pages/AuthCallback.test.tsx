import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { hashOAuthState } from "@/services/auth";
import { AuthCallback } from "./AuthCallback";

const loginWithOAuth = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ loginWithOAuth }),
}));

vi.mock("@/services/auth", () => ({
	hashOAuthState: vi.fn(),
}));

vi.mock("@/components/auth/AuthTransition", () => ({
	AuthTransition: () => null,
}));

function Landing() {
	const location = useLocation();
	return <output>{location.pathname + location.search}</output>;
}

async function landedAt() {
	return (await screen.findByRole("status")).textContent;
}

function renderCallback() {
	render(
		<MemoryRouter
			initialEntries={["/auth/callback/microsoft?code=c&state=s"]}
		>
			<Routes>
				<Route
					path="/auth/callback/:provider"
					element={<AuthCallback />}
				/>
				<Route path="*" element={<Landing />} />
			</Routes>
		</MemoryRouter>,
	);
}

beforeEach(() => {
	vi.clearAllMocks();
	sessionStorage.clear();
	vi.mocked(hashOAuthState).mockResolvedValue("digest");
	loginWithOAuth.mockResolvedValue(undefined);
	sessionStorage.setItem("oauth_state", "digest");
});

it("navigates to the path of a same-origin absolute return URL", async () => {
	sessionStorage.setItem(
		"oauth_redirect_from",
		`${window.location.origin}/mcp/callback?internal_state=abc`,
	);

	renderCallback();

	expect(await landedAt()).toBe("/mcp/callback?internal_state=abc");
	expect(loginWithOAuth).toHaveBeenCalledWith("microsoft", "c", "s");
	expect(sessionStorage.getItem("oauth_redirect_from")).toBeNull();
});

it("navigates to a stored relative path unchanged", async () => {
	sessionStorage.setItem("oauth_redirect_from", "/workflows?tab=runs");

	renderCallback();

	expect(await landedAt()).toBe("/workflows?tab=runs");
});

it("falls back to / for a cross-origin return URL", async () => {
	sessionStorage.setItem(
		"oauth_redirect_from",
		"https://evil.example.com/mcp/callback",
	);

	renderCallback();

	expect(await landedAt()).toBe("/");
});
