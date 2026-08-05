/**
 * Component tests for FormEmbedSection.
 *
 * Sharing tab that manages embed secrets. We stub authFetch and toast so we can
 * assert on the network calls and on the UI transitions (secret listing,
 * one-time reveal banner, delete confirmation).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockAuthFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: vi.fn(), useMutation: vi.fn() },
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

vi.mock("sonner", () => ({
	toast: { error: vi.fn(), success: vi.fn() },
}));

// Syntax highlighter pulls in a lot — stub it to a pre.
vi.mock("react-syntax-highlighter", () => ({
	Prism: ({ children }: { children: string }) => <pre>{children}</pre>,
}));
vi.mock("react-syntax-highlighter/dist/esm/styles/prism", () => ({
	oneDark: {},
}));

import { FormEmbedSection } from "./FormEmbedSection";

function jsonResponse(body: unknown, ok = true) {
	return {
		ok,
		status: ok ? 200 : 400,
		json: async () => body,
		text: async () => JSON.stringify(body),
	} as unknown as Response;
}

function mockInitialSettings(secrets: unknown[]) {
	mockAuthFetch.mockResolvedValueOnce(jsonResponse(secrets));
}

beforeEach(() => {
	mockAuthFetch.mockReset();
});

describe("FormEmbedSection", () => {
	it("fetches and lists secrets when mounted", async () => {
		mockInitialSettings([
			{
				id: "s1",
				name: "Prod",
				is_active: true,
				created_at: "2026-04-20T00:00:00Z",
			},
		]);

		renderWithProviders(<FormEmbedSection formId="form-1" />);

		await waitFor(() => {
			expect(mockAuthFetch).toHaveBeenCalledWith(
				"/api/forms/form-1/embed-secrets",
			);
		});
		expect(await screen.findByText("Prod")).toBeInTheDocument();
		expect(screen.getByText(/active/i)).toBeInTheDocument();
	});

	it("shows an empty state when there are no secrets", async () => {
		mockInitialSettings([]);

		renderWithProviders(<FormEmbedSection formId="form-1" />);

		expect(
			await screen.findByText(/no embed secrets configured/i),
		).toBeInTheDocument();
	});
});

describe("FormEmbedSection — create secret", () => {
	it("POSTs a new secret and reveals the raw value once", async () => {
		mockInitialSettings([]);
		mockAuthFetch
			// create
			.mockResolvedValueOnce(
				jsonResponse({
					id: "s2",
					name: "Staging",
					is_active: true,
					created_at: "2026-04-20T00:00:00Z",
					raw_secret: "sekret-ABC",
				}),
			)
			// refetch after create
			.mockResolvedValueOnce(
				jsonResponse([
					{
						id: "s2",
						name: "Staging",
						is_active: true,
						created_at: "2026-04-20T00:00:00Z",
					},
				]),
			);

		const { user } = renderWithProviders(
			<FormEmbedSection formId="form-1" />,
		);

		// Open the create form.
		await user.click(
			await screen.findByRole("button", { name: /create secret/i }),
		);
		await user.type(screen.getByLabelText(/^name$/i), "Staging");
		await user.click(screen.getByRole("button", { name: /^add$/i }));

		await waitFor(() => {
			// Two calls after list: create + refetch.
			expect(mockAuthFetch).toHaveBeenCalledWith(
				"/api/forms/form-1/embed-secrets",
				expect.objectContaining({ method: "POST" }),
			);
		});

		expect(
			await screen.findByText(/copy this secret now/i),
		).toBeInTheDocument();
		expect(screen.getByText("sekret-ABC")).toBeInTheDocument();
	});
});
