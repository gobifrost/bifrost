import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";

const mockUseCreateOAuthConnection = vi.fn();
const mockUseUpdateOAuthConnection = vi.fn();
const mockUseOAuthConnection = vi.fn();

vi.mock("@/hooks/useOAuth", () => ({
	useCreateOAuthConnection: () => mockUseCreateOAuthConnection(),
	useUpdateOAuthConnection: () => mockUseUpdateOAuthConnection(),
	useOAuthConnection: () => mockUseOAuthConnection(),
}));

vi.mock("@/components/oauth/OAuthProviderEditor", () => ({
	OAuthProviderEditor: ({
		formId,
		redirectUri,
		disabled,
		initialValues,
		onSubmit,
	}: {
		formId?: string;
		redirectUri?: string;
		disabled?: boolean;
		initialValues?: unknown;
		onSubmit: (data: unknown) => void;
	}) => (
		<div
			data-testid="oauth-provider-editor"
			data-formid={formId}
			data-redirecturi={redirectUri}
			data-disabled={String(Boolean(disabled))}
			data-initial={JSON.stringify(initialValues ?? null)}
		>
			<button
				type="button"
				onClick={() =>
					onSubmit({
						oauth_flow_type: "authorization_code",
						client_id: "client-1",
						client_secret: "secret-1",
						authorization_url: "https://auth.example.com",
						token_url: "https://token.example.com",
						scopes: "read write",
						audience: "https://api.example.com",
					})
				}
			>
				Submit
			</button>
		</div>
	),
}));

import { CreateOAuthConnectionDialog } from "./CreateOAuthConnectionDialog";

describe("CreateOAuthConnectionDialog", () => {
	beforeEach(() => {
		const refetch = vi.fn().mockResolvedValue(undefined);
		mockUseCreateOAuthConnection.mockReturnValue({
			mutateAsync: vi.fn().mockResolvedValue({ integration_id: "int-1" }),
			isPending: false,
		});
		mockUseUpdateOAuthConnection.mockReturnValue({
			mutateAsync: vi.fn().mockResolvedValue({ integration_id: "int-1" }),
			isPending: false,
		});
		mockUseOAuthConnection.mockReturnValue({
			data: undefined,
			isLoading: false,
			error: null,
			refetch,
		});
	});

	it("passes a canonical redirect URI and submits create payloads", async () => {
		const onOpenChange = vi.fn();
		const createMutation = mockUseCreateOAuthConnection();
		const { user } = renderWithProviders(
			<CreateOAuthConnectionDialog
				open
				onOpenChange={onOpenChange}
				integrationId="integration-1"
			/>,
		);

		expect(screen.getByRole("dialog")).toBeInTheDocument();
		expect(screen.getByText("Configure OAuth for Integration")).toBeInTheDocument();
		expect(screen.getByTestId("oauth-provider-editor")).toHaveAttribute(
			"data-formid",
			"oauth-connection-form",
		);
		expect(
			screen.getByTestId("oauth-provider-editor").getAttribute("data-redirecturi"),
		).toContain("/oauth/callback/integration-1");

		await user.click(screen.getByRole("button", { name: "Submit" }));

		expect(createMutation.mutateAsync).toHaveBeenCalledWith({
			body: {
				description: "",
				oauth_flow_type: "authorization_code",
				client_id: "client-1",
				client_secret: "secret-1",
				authorization_url: "https://auth.example.com",
				token_url: "https://token.example.com",
				scopes: "read write",
				integration_id: "integration-1",
				audience: "https://api.example.com",
			},
		});
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("names the connection instance whose OAuth credentials will change", () => {
		renderWithProviders(
			<CreateOAuthConnectionDialog
				open
				onOpenChange={vi.fn()}
				integrationId="integration-1"
				instanceContext="Microsoft 365 · Acme Production"
			/>,
		);
		expect(
			screen.getByText(/for Microsoft 365 · Acme Production/i),
		).toBeInTheDocument();
	});

	it("seeds edit mode from the existing connection and submits updates", async () => {
		const onOpenChange = vi.fn();
		const updateMutation = vi.fn().mockResolvedValue({ integration_id: "int-1" });
		mockUseOAuthConnection.mockReturnValue({
			data: {
				oauth_flow_type: "client_credentials",
				client_id: "client-2",
				authorization_url: "https://legacy.example.com/auth",
				token_url: "https://legacy.example.com/token",
				scopes: "scope-a, scope-b",
				audience: "https://api.example.com",
			},
			isLoading: false,
			error: null,
			refetch: vi.fn(),
		});
		mockUseUpdateOAuthConnection.mockReturnValue({
			mutateAsync: updateMutation,
			isPending: false,
		});

		const { user } = renderWithProviders(
			<CreateOAuthConnectionDialog
				open
				onOpenChange={onOpenChange}
				integrationId="integration-1"
				editConnectionName="connection-1"
			/>,
		);

		expect(screen.getByText("Edit OAuth Connection")).toBeInTheDocument();
		expect(screen.getByTestId("oauth-provider-editor")).toHaveAttribute(
			"data-disabled",
			"false",
		);
		expect(
			screen.getByTestId("oauth-provider-editor").getAttribute("data-initial"),
		).toContain('"oauth_flow_type":"client_credentials"');

		await user.click(screen.getByRole("button", { name: "Submit" }));

		expect(updateMutation).toHaveBeenCalledWith({
			params: { path: { connection_name: "connection-1" } },
			body: {
				oauth_flow_type: "authorization_code",
				client_id: "client-1",
				client_secret: "secret-1",
				authorization_url: "https://auth.example.com",
				token_url: "https://token.example.com",
				scopes: ["read", "write"],
				audience: "https://api.example.com",
			},
		});
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("shows a loading state before editing an existing connection", () => {
		mockUseOAuthConnection.mockReturnValue({
			data: undefined,
			isLoading: true,
			error: null,
			refetch: vi.fn(),
		});

		renderWithProviders(
			<CreateOAuthConnectionDialog
				open
				onOpenChange={vi.fn()}
				integrationId="integration-1"
				editConnectionName="connection-1"
			/>,
		);

		expect(
			screen.getByText("Loading connection details…"),
		).toBeInTheDocument();
		expect(
			screen.queryByTestId("oauth-provider-editor"),
		).not.toBeInTheDocument();
	});

	it("shows connection loading errors and retries the query", async () => {
		const refetch = vi.fn().mockResolvedValue(undefined);
		mockUseOAuthConnection.mockReturnValue({
			data: undefined,
			isLoading: false,
			error: new Error("network down"),
			refetch,
		});

		const { user } = renderWithProviders(
			<CreateOAuthConnectionDialog
				open
				onOpenChange={vi.fn()}
				integrationId="integration-1"
				editConnectionName="connection-1"
			/>,
		);

		expect(
			screen.getByText("Could not load the existing connection"),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Retry loading connection" }),
		);
		expect(refetch).toHaveBeenCalledOnce();
	});

	it("keeps the dialog open and shows an inline error when submit fails", async () => {
		const onOpenChange = vi.fn();
		mockUseCreateOAuthConnection.mockReturnValue({
			mutateAsync: vi.fn().mockRejectedValue(new Error("create failed")),
			isPending: false,
		});

		const { user } = renderWithProviders(
			<CreateOAuthConnectionDialog
				open
				onOpenChange={onOpenChange}
				integrationId="integration-1"
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Submit" }));

		expect(onOpenChange).not.toHaveBeenCalledWith(false);
		expect(await screen.findByRole("alert")).toHaveTextContent("create failed");
		expect(screen.getByRole("dialog")).toBeInTheDocument();
	});

	it("blocks dismissal while a connection save is pending", () => {
		const onOpenChange = vi.fn();
		mockUseCreateOAuthConnection.mockReturnValue({
			mutateAsync: vi.fn(),
			isPending: true,
		});

		renderWithProviders(
			<CreateOAuthConnectionDialog
				open
				onOpenChange={onOpenChange}
				integrationId="integration-1"
			/>,
		);

		expect(
			screen.queryByRole("button", { name: /close/i }),
		).not.toBeInTheDocument();
		const overlay = document.querySelector(
			'[data-slot="dialog-overlay"]',
		) as HTMLElement | null;
		expect(overlay).toBeTruthy();
		if (overlay) {
			fireEvent.pointerDown(overlay);
			fireEvent.keyDown(document, { key: "Escape" });
		}
		expect(onOpenChange).not.toHaveBeenCalledWith(false);
	});
});
