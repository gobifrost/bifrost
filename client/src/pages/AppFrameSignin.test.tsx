import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { AppFrameGate } from "./AppFrameSignin";

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isLoading: false, isAuthenticated: false }),
}));
vi.mock("@/lib/auth-token", () => ({
	isAppFrame: () => true,
	setFrameSession: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({ getCsrfToken: () => null }));

afterEach(() => vi.restoreAllMocks());

it("directs the user to allow popups when the browser blocks the sign-in window", async () => {
	vi.spyOn(window, "open").mockReturnValue(null);
	render(
		<AppFrameGate>
			<p>Protected App</p>
		</AppFrameGate>,
	);
	await userEvent.click(
		screen.getByRole("button", { name: "Sign In to Bifrost" }),
	);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Allow popups for this site and try again.",
	);
	expect(screen.queryByText("Protected App")).toBeNull();
});
