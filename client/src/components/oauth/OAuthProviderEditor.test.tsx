import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { copyToClipboard } from "@/lib/clipboard";
import { toast } from "sonner";

import { OAuthProviderEditor } from "./OAuthProviderEditor";

vi.mock("@/lib/clipboard", () => ({
	copyToClipboard: vi.fn(),
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

describe("OAuthProviderEditor", () => {
	beforeEach(() => {
		vi.mocked(copyToClipboard).mockResolvedValue(true);
		vi.mocked(toast.success).mockClear();
		vi.mocked(toast.error).mockClear();
	});

	it("renders the redirect guidance with an accessible copy button", async () => {
		const { user } = renderWithProviders(
			<>
				<OAuthProviderEditor
					flowType="authorization_code"
					redirectUri="https://app.example.com/oauth/callback/integration-1"
					onSubmit={vi.fn()}
					formId="oauth-provider-form"
				/>
			</>,
		);

		expect(
			screen.getByText(/your redirect uri/i),
		).toBeInTheDocument();
		const copyButton = screen.getByRole("button", {
			name: "Copy redirect URI",
		});
		expect(copyButton).toBeEnabled();
		await user.click(copyButton);
		expect(vi.mocked(copyToClipboard)).toHaveBeenCalledWith(
			"https://app.example.com/oauth/callback/integration-1",
		);
		await screen.findByRole("button", { name: "Redirect URI copied" });
	});

	it("shows copy failure feedback without claiming success", async () => {
		vi.mocked(copyToClipboard).mockResolvedValue(false);

		const { user } = renderWithProviders(
			<>
				<OAuthProviderEditor
					flowType="authorization_code"
					redirectUri="https://app.example.com/oauth/callback/integration-1"
					onSubmit={vi.fn()}
					formId="oauth-provider-form"
				/>
			</>,
		);

		await user.click(
			screen.getByRole("button", { name: "Copy redirect URI" }),
		);

		expect(vi.mocked(toast.success)).not.toHaveBeenCalled();
		expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
			"Could not copy redirect URI",
		);
		expect(
			await screen.findByText("Could not copy the redirect URI. Copy it manually."),
		).toBeInTheDocument();
	});

	it("clears copied state when the redirect URI changes", async () => {
		const { user, rerender } = renderWithProviders(
			<>
				<OAuthProviderEditor
					flowType="authorization_code"
					redirectUri="https://app.example.com/oauth/callback/integration-1"
					onSubmit={vi.fn()}
					formId="oauth-provider-form"
				/>
			</>,
		);

		await user.click(screen.getByRole("button", { name: "Copy redirect URI" }));
		await screen.findByRole("button", { name: "Redirect URI copied" });

		rerender(
			<>
				<OAuthProviderEditor
					flowType="authorization_code"
					redirectUri="https://app.example.com/oauth/callback/integration-2"
					onSubmit={vi.fn()}
					formId="oauth-provider-form"
				/>
			</>,
		);

		expect(
			screen.getByRole("button", { name: "Copy redirect URI" }),
		).toBeInTheDocument();
		expect(
			screen.queryByText("Could not copy the redirect URI. Copy it manually."),
		).not.toBeInTheDocument();
	});

	it("submits a client credentials payload and clears the authorization URL", async () => {
		const onSubmit = vi.fn();
		const { user } = renderWithProviders(
			<>
				<OAuthProviderEditor
					flowType="client_credentials"
					flowTypeSwitchable={false}
					onSubmit={onSubmit}
					formId="oauth-provider-form"
				/>
				<button type="submit" form="oauth-provider-form">
					Save
				</button>
			</>,
		);

		await user.type(screen.getByLabelText(/client id/i), "client-123");
		await user.type(screen.getByLabelText(/client secret/i), "secret-123");
		await user.type(screen.getByLabelText(/token url/i), "https://token.example.com");
		await user.type(screen.getByLabelText(/audience/i), "https://api.example.com");
		await user.type(screen.getByLabelText(/scopes/i), "read write");
		await user.click(screen.getByRole("button", { name: "Save" }));

		expect(onSubmit).toHaveBeenCalledWith(
			expect.objectContaining({
				oauth_flow_type: "client_credentials",
				client_id: "client-123",
				client_secret: "secret-123",
				authorization_url: null,
				token_url: "https://token.example.com",
				scopes: "read write",
				audience: "https://api.example.com",
			}),
		);
	});

	it("offers workload identity only when explicitly enabled", async () => {
		const { user } = renderWithProviders(
			<OAuthProviderEditor
				flowType="authorization_code"
				allowWorkloadIdentity
				onSubmit={vi.fn()}
			/>,
		);

		await user.click(screen.getByRole("combobox"));
		expect(
			screen.getByRole("option", { name: "Workload Identity" }),
		).toBeInTheDocument();
		await user.click(screen.getByRole("option", { name: "Workload Identity" }));

		expect(screen.getByLabelText("Tenant ID")).toBeInTheDocument();
		expect(screen.queryByLabelText(/client secret/i)).not.toBeInTheDocument();
		expect(screen.queryByLabelText(/token url/i)).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: "Authentication is handled by your workflow’s provider SDK using a configured runtime identity. Configure identity trust and permissions externally. Bifrost does not obtain or refresh tokens in this mode.",
			}),
		).toBeInTheDocument();
	});

	it("warns before an existing connection switches to workload identity", async () => {
		const { user } = renderWithProviders(
			<OAuthProviderEditor
				flowType="authorization_code"
				allowWorkloadIdentity
				isEditMode
				onSubmit={vi.fn()}
			/>,
		);
		await user.click(screen.getByRole("combobox"));
		await user.click(
			screen.getByRole("option", { name: "Workload Identity" }),
		);
		expect(
			screen.getByText(
				"Switching to Workload Identity removes this connection’s stored OAuth credentials. Workflows must use their provider SDK.",
			),
		).toBeInTheDocument();
	});
	it("ignores a clipboard operation completing after unmount", async () => {
		let finish!: (value: boolean) => void;
		vi.mocked(copyToClipboard).mockImplementationOnce(() => new Promise<boolean>(resolve => { finish = resolve; }));
		const { user, unmount } = renderWithProviders(<OAuthProviderEditor flowType="authorization_code" redirectUri="https://example.test/callback" onSubmit={vi.fn()} />);
		await user.click(screen.getByRole("button", {name:"Copy redirect URI"}));
		unmount();
		finish(true);
		await Promise.resolve();
		expect(toast.success).not.toHaveBeenCalled();
	});

});
