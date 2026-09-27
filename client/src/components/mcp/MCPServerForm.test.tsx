import { act } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { MCPServerForm } from "./MCPServerForm";
const mutateAsync = vi.hoisted(() => vi.fn());
const discover = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({
	$api: { useMutation: () => ({ mutateAsync }) },
	apiClient: { POST: discover },
}));

it("retains the draft after failure and locks the pending retry", async () => {
	let release!: (value: unknown) => void;
	mutateAsync
		.mockRejectedValueOnce(new Error("Synthetic creation failure"))
		.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					release = resolve;
				}),
		);
	const onCancel = vi.fn(),
		onSuccess = vi.fn(),
		onPendingChange = vi.fn();
	const { user } = renderWithProviders(
		<MCPServerForm
			onCancel={onCancel}
			onSuccess={onSuccess}
			onPendingChange={onPendingChange}
		/>,
	);
	await user.type(screen.getByLabelText("Display name"), "Review server");
	await user.type(
		screen.getByLabelText("Server URL"),
		"https://example.invalid/mcp",
	);
	await user.click(screen.getByRole("button", { name: "Create Server" }));
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Synthetic creation failure",
	);
	expect(screen.getByRole("alert")).toHaveFocus();
	expect(screen.getByLabelText("Display name")).toHaveValue("Review server");
	await user.click(screen.getByRole("button", { name: "Retry creation" }));
	expect(screen.getByLabelText("Display name")).toBeDisabled();
	expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
	await user.click(screen.getByRole("button", { name: "Cancel" }));
	expect(onCancel).not.toHaveBeenCalled();
	expect(onPendingChange).toHaveBeenLastCalledWith(true);
	await act(async () => release({ id: "review-server" }));
	expect(onSuccess).toHaveBeenCalledWith("review-server");
	expect(onPendingChange).toHaveBeenLastCalledWith(false);
	expect(mutateAsync).toHaveBeenCalledTimes(2);
	expect(mutateAsync.mock.calls[1][0]).toEqual(mutateAsync.mock.calls[0][0]);
});

it("discovers client credentials and submits manual overrides with the selected flow", async () => {
	mutateAsync.mockReset().mockResolvedValue({ id: "created" });
	discover.mockResolvedValue({
		data: {
			metadata: {
				token_endpoint: "https://example.invalid/token",
				scopes_supported: ["read", "write"],
				grant_types_supported: ["client_credentials"],
			},
		},
	});
	const { user } = renderWithProviders(<MCPServerForm onSuccess={vi.fn()} />);
	await user.type(screen.getByLabelText("Display name"), "Discovery server");
	await user.type(
		screen.getByLabelText("Server URL"),
		"https://example.invalid/mcp",
	);
	await user.click(
		screen.getByRole("button", { name: "Discover OAuth metadata" }),
	);
	expect(await screen.findByLabelText("Token URL")).toHaveValue(
		"https://example.invalid/token",
	);
	expect(screen.getByLabelText("Token URL")).toHaveAttribute("readonly");
	expect(
		screen.getByRole("combobox", { name: "OAuth flow" }),
	).toHaveTextContent("Client credentials");
	expect(
		screen.queryByLabelText("Authorization URL"),
	).not.toBeInTheDocument();
	await user.click(
		screen.getByRole("button", {
			name: "Override discovered values manually",
		}),
	);
	await user.clear(screen.getByLabelText("Token URL"));
	await user.type(
		screen.getByLabelText("Token URL"),
		"https://example.invalid/manual-token",
	);
	await user.click(screen.getByRole("button", { name: "Create Server" }));
	expect(mutateAsync).toHaveBeenCalledWith(
		expect.objectContaining({
			body: expect.objectContaining({
				oauth_provider: expect.objectContaining({
					oauth_flow_type: "client_credentials",
					token_url: "https://example.invalid/manual-token",
					authorization_url: null,
					scopes: ["read", "write"],
				}),
			}),
		}),
	);
});

it("keeps discovery failure guidance beside editable, labelled OAuth fields", async () => {
	discover.mockResolvedValue({ error: { detail: "Unavailable" } });
	const { user } = renderWithProviders(<MCPServerForm />);
	await user.type(
		screen.getByLabelText("Server URL"),
		"https://example.invalid/mcp",
	);
	await user.click(
		screen.getByRole("button", { name: "Discover OAuth metadata" }),
	);
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Retry discovery or enter the values manually below.",
	);
	expect(screen.getByLabelText("Authorization URL")).not.toHaveAttribute(
		"readonly",
	);
	expect(screen.getByLabelText(/Redirect URL/)).toHaveAttribute("readonly");
});

it("reads endpoints from nested authorization-server metadata, not attacker-shaped flat fields", async () => {
	mutateAsync.mockReset().mockResolvedValue({ id: "created" });
	discover.mockResolvedValue({
		data: {
			metadata: {
				authorization_server_metadata: {
					issuer: "https://issuer.example.com",
					authorization_endpoint:
						"https://issuer.example.com/authorize",
					token_endpoint: "https://issuer.example.com/token",
				},
				protected_resource_metadata: {
					resource: "https://resource.example.com/mcp",
				},
				// Attacker-controlled flat fields must not win over nested docs.
				issuer: "https://attacker.example.com",
				authorization_endpoint: "https://attacker.example.com/authorize",
				token_endpoint: "https://attacker.example.com/token",
			},
		},
	});
	const { user } = renderWithProviders(<MCPServerForm />);
	await user.type(screen.getByLabelText("Display name"), "Nested MCP");
	await user.type(
		screen.getByLabelText("Server URL"),
		"https://resource.example.com/mcp",
	);
	await user.click(
		screen.getByRole("button", { name: "Discover OAuth metadata" }),
	);
	expect(await screen.findByLabelText("Token URL")).toHaveValue(
		"https://issuer.example.com/token",
	);
	expect(screen.getByLabelText("Authorization URL")).toHaveValue(
		"https://issuer.example.com/authorize",
	);
	expect(screen.getByLabelText("Audience / resource indicator")).toHaveValue(
		"https://resource.example.com/mcp",
	);
});

it("reads scopes from nested authorization-server metadata", async () => {
	mutateAsync.mockReset().mockResolvedValue({ id: "created" });
	discover.mockResolvedValue({
		data: {
			metadata: {
				authorization_endpoint: "https://issuer.example.com/authorize",
				token_endpoint: "https://issuer.example.com/token",
				authorization_server_metadata: {
					issuer: "https://issuer.example.com",
					authorization_endpoint:
						"https://issuer.example.com/authorize",
					token_endpoint: "https://issuer.example.com/token",
					scopes_supported: ["mcp:access", "profile"],
				},
				protected_resource_metadata: {
					resource: "https://resource.example.com/mcp",
				},
			},
		},
	});
	const { user } = renderWithProviders(<MCPServerForm onSuccess={vi.fn()} />);
	await user.type(screen.getByLabelText("Display name"), "Scoped MCP");
	await user.type(
		screen.getByLabelText("Server URL"),
		"https://resource.example.com/mcp",
	);
	await user.click(
		screen.getByRole("button", { name: "Discover OAuth metadata" }),
	);
	expect(await screen.findByLabelText("Scopes")).toHaveValue(
		"mcp:access profile",
	);
	await user.click(screen.getByRole("button", { name: "Create Server" }));
	expect(mutateAsync).toHaveBeenCalledWith(
		expect.objectContaining({
			body: expect.objectContaining({
				oauth_provider: expect.objectContaining({
					scopes: ["mcp:access", "profile"],
				}),
			}),
		}),
	);
});
