import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { authFetch } from "@/lib/api-client";
import { MCPCallback } from "./MCPCallback";

vi.mock("@/lib/api-client", () => ({ authFetch: vi.fn() }));
vi.mock("@/components/auth/AuthTransition", () => ({
	AuthTransition: () => null,
}));

afterEach(() => {
	vi.clearAllMocks();
});

it("delegates the cookie-authenticated callback to challenge-aware authFetch", async () => {
	vi.mocked(authFetch).mockResolvedValue(Response.json({}));

	render(
		<MemoryRouter initialEntries={["/mcp/callback?internal_state=state-1"]}>
			<MCPCallback />
		</MemoryRouter>,
	);

	await waitFor(() =>
		expect(authFetch).toHaveBeenCalledWith(
			"/mcp/callback?internal_state=state-1",
			{ headers: { Accept: "application/json" } },
		),
	);
	expect(await screen.findByText("No redirect URL returned from server")).toBeVisible();
});
