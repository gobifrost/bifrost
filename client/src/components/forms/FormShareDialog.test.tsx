import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockAuthFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: vi.fn(), useMutation: vi.fn() },
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

vi.mock("sonner", () => ({
	toast: { error: vi.fn(), success: vi.fn() },
}));
vi.mock("react-syntax-highlighter", () => ({
	Prism: ({ children }: { children: string }) => <pre>{children}</pre>,
}));
vi.mock("react-syntax-highlighter/dist/esm/styles/prism", () => ({
	oneDark: {},
}));
vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: ({
		content,
		onChange,
		ariaLabel,
	}: {
		content: string;
		onChange?: (value: string) => void;
		ariaLabel?: string;
	}) => (
		<textarea
			aria-label={ariaLabel}
			value={content}
			onChange={(event) => onChange?.(event.target.value)}
		/>
	),
}));

import { FormShareDialog } from "./FormShareDialog";

function jsonResponse(body: unknown, ok = true) {
	return {
		ok,
		status: ok ? 200 : 400,
		json: async () => body,
		text: async () => JSON.stringify(body),
	} as unknown as Response;
}

const review = {
	fingerprint: "sha256:reviewed",
	blockers: [],
	warnings: [],
	submission_workflow: { ref: "submit.py::submit", name: "Submit request" },
	startup_workflow: null,
	provider_fields: [
		{
			field_name: "company",
			provider_ref: "providers.py::companies",
			provider_name: "Companies",
		},
	],
	file_fields: [],
};

const unpublished = {
	form_id: "form-1",
	status: "unpublished",
	public_key: null,
	allowed_origins: [],
	spam_protection_enabled: true,
	approved_fingerprint: null,
	current_fingerprint: "sha256:reviewed",
	iframe_path: null,
	warnings: [],
	blockers: [],
};

const form = {
	id: "form-1",
	name: "Customer intake",
	confirmation_markdown: "## Form submitted\n\nThank you!",
};

beforeEach(() => {
	mockAuthFetch.mockReset();
});

describe("FormShareDialog", () => {
	it("shows the private link and requires capability confirmation before publishing", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(jsonResponse(unpublished))
			.mockResolvedValueOnce(jsonResponse(review))
			.mockResolvedValueOnce(jsonResponse(form))
			.mockResolvedValueOnce(jsonResponse({ ok: true }))
			.mockResolvedValueOnce(
				jsonResponse({
					...unpublished,
					status: "published",
					public_key: "public-key",
					allowed_origins: ["https://example.com"],
					approved_fingerprint: "sha256:reviewed",
					iframe_path: "/embed/forms/public/public-key",
				}),
			)
			.mockResolvedValueOnce(jsonResponse(review))
			.mockResolvedValueOnce(jsonResponse(form));

		const { user } = renderWithProviders(
			<FormShareDialog
				formId="form-1"
				formName="Customer intake"
				open
				onOpenChange={vi.fn()}
			/>,
		);

		expect(await screen.findByLabelText("Private form link")).toHaveValue(
			"http://localhost:3000/execute/form-1",
		);
		expect(
			screen.queryByLabelText("Allowed Website Origins"),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("heading", { name: "Confirmation Message" }),
		).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Copy private link" }),
		);
		expect(await navigator.clipboard.readText()).toBe(
			"http://localhost:3000/execute/form-1",
		);
		await user.click(screen.getByRole("tab", { name: "Website Embed" }));
		await user.click(
			screen.getByRole("button", { name: /Website Restrictions/ }),
		);
		await user.type(
			screen.getByLabelText("Allowed Website Origins"),
			"https://example.com",
		);
		await user.click(screen.getByRole("switch", { name: "Not Published" }));

		expect(
			screen.getByRole("heading", {
				name: "Allow anonymous form access?",
			}),
		).toBeInTheDocument();
		expect(screen.getByText(/execute submit request/i)).toBeInTheDocument();
		expect(
			screen.getByText(/query 1 approved data provider/i),
		).toHaveTextContent("Companies");
		expect(
			screen.getByText(
				/no other workflows or bifrost execution apis are granted/i,
			),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Publish public embed" }),
		);

		await waitFor(() => {
			expect(mockAuthFetch).toHaveBeenCalledWith(
				"/api/forms/form-1/publication",
				expect.objectContaining({
					method: "PUT",
					body: JSON.stringify({
						reviewed_fingerprint: "sha256:reviewed",
						allowed_origins: ["https://example.com"],
						spam_protection_enabled: true,
					}),
				}),
			);
		});
		const embedCode = await screen.findByLabelText("Embed Code");
		expect(embedCode).toHaveTextContent(
			"/embed/forms/public/public-key?theme=light&header=true&background=solid",
		);
		expect(screen.getByText("Shown")).toBeInTheDocument();
		expect(screen.getByText("Solid")).toBeInTheDocument();
	});

	it("updates embed appearance and opens the disable confirmation", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(
				jsonResponse({
					...unpublished,
					status: "published",
					public_key: "public-key",
					approved_fingerprint: "sha256:reviewed",
					iframe_path: "/embed/forms/public/public-key",
				}),
			)
			.mockResolvedValueOnce(jsonResponse(review))
			.mockResolvedValueOnce(jsonResponse(form));

		const { user } = renderWithProviders(
			<FormShareDialog
				formId="form-1"
				formName="Customer intake"
				open
				onOpenChange={vi.fn()}
			/>,
		);

		await screen.findByLabelText("Private form link");
		await user.click(screen.getByRole("tab", { name: "Website Embed" }));
		const embedCode = await screen.findByLabelText("Embed Code");

		await user.click(screen.getByRole("combobox", { name: "Theme" }));
		await user.click(screen.getByRole("option", { name: "Dark" }));
		await user.click(screen.getByRole("switch", { name: "Show Header" }));
		await user.click(
			screen.getByRole("switch", { name: "Transparent Background" }),
		);

		expect(embedCode).toHaveTextContent(
			"/embed/forms/public/public-key?theme=dark&header=false&background=transparent",
		);
		expect(screen.getByText("Hidden")).toBeInTheDocument();
		expect(screen.getByText("Transparent")).toBeInTheDocument();
		const rotateButton = screen.getByRole("button", { name: "Rotate" });
		expect(rotateButton.parentElement).toHaveClass("justify-end");

		await user.click(screen.getByRole("switch", { name: "Published" }));
		expect(
			screen.getByRole("heading", { name: "Disable the public embed?" }),
		).toBeInTheDocument();
	});

	it("hides stale embed code and explains that capability changes pause it", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(
				jsonResponse({
					...unpublished,
					status: "needs_review",
					public_key: "stale-key",
					iframe_path: "/embed/forms/public/stale-key",
				}),
			)
			.mockResolvedValueOnce(jsonResponse(review))
			.mockResolvedValueOnce(jsonResponse(form));

		const { user } = renderWithProviders(
			<FormShareDialog
				formId="form-1"
				formName="Customer intake"
				open
				onOpenChange={vi.fn()}
			/>,
		);

		await screen.findByLabelText("Private form link");
		expect(
			screen.queryByRole("heading", { name: "Confirmation Message" }),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: "Website Embed" }));
		expect(
			await screen.findByText(/existing embed is paused/i),
		).toBeInTheDocument();
		expect(screen.queryByLabelText("Embed Code")).not.toBeInTheDocument();
		expect(
			screen.getByRole("switch", { name: "Review Required" }),
		).not.toBeChecked();
		expect(screen.getByRole("button", { name: "Update" })).toBeDisabled();
	});

	it("saves the Confirmation Message from the sharing surface", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(jsonResponse(unpublished))
			.mockResolvedValueOnce(jsonResponse(review))
			.mockResolvedValueOnce(jsonResponse(form))
			.mockResolvedValueOnce(jsonResponse({ ...form }));

		const { user } = renderWithProviders(
			<FormShareDialog
				formId="form-1"
				formName="Customer intake"
				open
				onOpenChange={vi.fn()}
			/>,
		);

		await screen.findByLabelText("Private form link");
		expect(
			screen.queryByRole("heading", { name: "Confirmation Message" }),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: "Website Embed" }));
		const markdown = await screen.findByLabelText(
			"Confirmation Message editor",
		);
		await user.clear(markdown);
		await user.type(markdown, "## Received\n\nWe will follow up soon.");
		await user.click(screen.getByRole("tab", { name: "Preview" }));
		expect(screen.getByRole("heading", { name: "Received" })).toBeVisible();
		expect(screen.getByText("We will follow up soon.")).toBeVisible();
		await user.click(screen.getByRole("button", { name: "Update" }));

		await waitFor(() => {
			expect(mockAuthFetch).toHaveBeenCalledWith(
				"/api/forms/form-1",
				expect.objectContaining({
					method: "PATCH",
					body: JSON.stringify({
						confirmation_markdown:
							"## Received\n\nWe will follow up soon.",
					}),
				}),
			);
		});
	});

	it("loads HMAC secret management in its own tab", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(jsonResponse(unpublished))
			.mockResolvedValueOnce(jsonResponse(review))
			.mockResolvedValueOnce(jsonResponse(form))
			.mockResolvedValueOnce(jsonResponse([]));

		const { user } = renderWithProviders(
			<FormShareDialog
				formId="form-1"
				formName="Customer intake"
				open
				onOpenChange={vi.fn()}
			/>,
		);

		await screen.findByLabelText("Private form link");
		await user.click(screen.getByRole("tab", { name: "HMAC" }));
		expect(
			await screen.findByText("No embed secrets configured."),
		).toBeInTheDocument();
		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/forms/form-1/embed-secrets",
		);
		expect(
			screen.queryByRole("heading", { name: "Confirmation Message" }),
		).not.toBeInTheDocument();
	});

	it("automatically saves Website Restrictions for a published embed", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(
				jsonResponse({
					...unpublished,
					status: "published",
					public_key: "public-key",
					approved_fingerprint: "sha256:reviewed",
					iframe_path: "/embed/forms/public/public-key",
				}),
			)
			.mockResolvedValueOnce(jsonResponse(review))
			.mockResolvedValueOnce(jsonResponse(form))
			.mockResolvedValueOnce(jsonResponse({ ok: true }));

		const { user } = renderWithProviders(
			<FormShareDialog
				formId="form-1"
				formName="Customer intake"
				open
				onOpenChange={vi.fn()}
			/>,
		);

		await screen.findByLabelText("Private form link");
		await user.click(screen.getByRole("tab", { name: "Website Embed" }));
		await user.click(
			screen.getByRole("button", { name: /Website Restrictions/ }),
		);
		await user.type(
			screen.getByLabelText("Allowed Website Origins"),
			"https://example.com",
		);

		await waitFor(
			() => {
				expect(mockAuthFetch).toHaveBeenCalledWith(
					"/api/forms/form-1/publication",
					expect.objectContaining({
						method: "PUT",
						body: JSON.stringify({
							reviewed_fingerprint: "sha256:reviewed",
							allowed_origins: ["https://example.com"],
							spam_protection_enabled: true,
						}),
					}),
				);
			},
			{ timeout: 2000 },
		);
		expect(
			await screen.findByText("Restrictions saved"),
		).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Update" })).toBeDisabled();
	});

	it("saves Spam Protection immediately for a published embed", async () => {
		const published = {
			...unpublished,
			status: "published",
			public_key: "public-key",
			approved_fingerprint: "sha256:reviewed",
			iframe_path: "/embed/forms/public/public-key",
		};
		mockAuthFetch
			.mockResolvedValueOnce(jsonResponse(published))
			.mockResolvedValueOnce(jsonResponse(review))
			.mockResolvedValueOnce(jsonResponse(form))
			.mockResolvedValueOnce(
				jsonResponse({ ...published, spam_protection_enabled: false }),
			);

		const { user } = renderWithProviders(
			<FormShareDialog
				formId="form-1"
				formName="Customer intake"
				open
				onOpenChange={vi.fn()}
			/>,
		);

		await screen.findByLabelText("Private form link");
		await user.click(screen.getByRole("tab", { name: "Website Embed" }));
		const spamProtection = screen.getByRole("switch", {
			name: "Spam Protection",
		});
		expect(spamProtection).toBeChecked();
		await user.click(spamProtection);

		await waitFor(() => {
			expect(mockAuthFetch).toHaveBeenCalledWith(
				"/api/forms/form-1/publication",
				expect.objectContaining({
					method: "PUT",
					body: JSON.stringify({
						reviewed_fingerprint: "sha256:reviewed",
						allowed_origins: [],
						spam_protection_enabled: false,
					}),
				}),
			);
		});
	});
});
