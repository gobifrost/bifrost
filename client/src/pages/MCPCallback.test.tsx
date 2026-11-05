import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api-client";
import { MCPCallback } from "./MCPCallback";
const { mockGet, mockPost } = vi.hoisted(() => ({
	mockGet: vi.fn(),
	mockPost: vi.fn(),
}));
const authState = {
	isAuthenticated: true,
	isLoading: false,
	user: { email: "user@example.com" },
};
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => authState }));
vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: mockGet, POST: mockPost },
}));
vi.mock("@/components/auth/AuthTransition", () => ({
	AuthTransition: () => null,
}));
beforeEach(() => {
	vi.clearAllMocks();
	authState.isAuthenticated = true;
});
const details = {
	client_name: "Unnamed App",
	redirect_uri: "https://app.example/cb",
	consent_token: "a".repeat(64),
};
it("displays the registered callback without approving on load", async () => {
	mockGet.mockResolvedValue({ data: details, response: new Response() });
	renderWithProviders(<MCPCallback />, {
		initialEntries: ["/connect-app?request=state-1"],
	});
	expect(
		await screen.findByRole("heading", {
			name: "Allow Unnamed App to Access Bifrost?",
		}),
	).toBeVisible();
	expect(screen.getByText(details.redirect_uri)).toBeVisible();
	expect(screen.getByText("This app did not provide a name.")).toBeVisible();
	expect(apiClient.POST).not.toHaveBeenCalled();
});
it("posts denial only after a deliberate choice", async () => {
	mockGet.mockResolvedValue({ data: details, response: new Response() });
	mockPost.mockRejectedValue(new Error("Connection expired"));
	const { user } = renderWithProviders(<MCPCallback />, {
		initialEntries: ["/mcp/callback?internal_state=state-2"],
	});
	await screen.findByRole("heading", {
		name: "Allow Unnamed App to Access Bifrost?",
	});
	await user.click(screen.getByRole("button", { name: "Deny" }));
	await waitFor(() =>
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/auth/app-connections/{request_id}",
			{
				params: { path: { request_id: "state-2" } },
				body: { approved: false, consent_token: details.consent_token },
			},
		),
	);
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Connection expired",
	);
});
it("shows an expired request without an approval button", async () => {
	mockGet.mockResolvedValue({
		error: { detail: "Connection expired" },
		response: new Response(null, { status: 404 }),
	});
	renderWithProviders(<MCPCallback />, {
		initialEntries: ["/connect-app?request=expired"],
	});
	expect(await screen.findByRole("alert")).toHaveTextContent("expired");
	expect(
		screen.queryByRole("button", { name: "Allow" }),
	).not.toBeInTheDocument();
});
