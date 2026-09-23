import { describe, expect, it, vi, beforeEach } from "vitest";
import {
	act,
	renderWithProviders,
	screen,
	waitFor,
	within,
} from "@/test-utils";

const mockUseMediaQuery = vi.fn();
const mockUseAuth = vi.fn();
const mockUseOrganizations = vi.fn();
const mockAuthFetch = vi.fn();
const mockExportEntities = vi.fn();

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: (...args: unknown[]) => mockUseMediaQuery(...args),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (...args: unknown[]) => mockUseOrganizations(...args),
}));

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

vi.mock("@/services/exportImport", () => ({
	exportEntities: (...args: unknown[]) => mockExportEntities(...args),
}));

vi.mock("@/components/knowledge/KnowledgeDocumentDrawer", () => ({
	KnowledgeDocumentDrawer: ({
		namespace,
		documentId,
		isCreating,
	}: {
		namespace: string;
		documentId: string | null;
		isCreating: boolean;
	}) =>
		documentId || isCreating ? (
			<div role="dialog">
				Knowledge drawer {namespace || "new"} {documentId || "create"}
			</div>
		) : null,
}));

vi.mock("@/components/ImportDialog", () => ({
	ImportDialog: () => null,
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

import { Knowledge } from "./Knowledge";

const namespaces = [
	{ namespace: "support", document_count: 32 },
	{ namespace: "sales", document_count: 18 },
];

const organizations = [
	{
		id: "org-acme",
		name: "Acme Corp",
		domain: "acme.test",
		is_active: true,
		is_provider: false,
		settings: {},
		created_at: "2026-06-01T00:00:00Z",
		created_by: "user-1",
		updated_at: "2026-06-01T00:00:00Z",
	},
];

function makeDocs() {
	return Array.from({ length: 50 }, (_, index) => {
		const even = index % 2 === 0;
		const serial = String(index + 1).padStart(2, "0");
		return {
			id: `doc-${serial}`,
			namespace: even ? "support" : "sales",
			key: even ? `support-${serial}` : `sales-${serial}`,
			content_preview: even
				? `Support summary ${serial} with escalation steps, replacement notes, and handoff guidance.`
				: `Sales summary ${serial} with objection handling, pricing context, and follow-up guidance.`,
			metadata: { fixture: serial },
			organization_id: even ? null : "org-acme",
			created_at: `2026-08-${String((index % 28) + 1).padStart(2, "0")}T12:00:00Z`,
		};
	});
}

describe("Knowledge", () => {
	beforeEach(() => {
		mockUseMediaQuery.mockReturnValue(true);
		mockUseAuth.mockReturnValue({
			user: { id: "user-1" },
			isPlatformAdmin: true,
		});
		mockUseOrganizations.mockReturnValue({ data: organizations });
		mockExportEntities.mockResolvedValue(undefined);
		mockAuthFetch.mockImplementation(
			async (input: unknown, options?: { method?: string }) => {
				const url =
					typeof input === "string"
						? input
						: input && typeof input === "object" && "url" in input
							? String((input as Request).url)
							: String(input);
				if (
					url.includes("/api/knowledge-sources/") &&
					url.includes("/documents/") &&
					options?.method === "DELETE"
				) {
					return new Response(null, { status: 204 });
				}
				if (url.startsWith("/api/knowledge-sources/documents")) {
					return new Response(JSON.stringify(makeDocs()), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}
				if (url === "/api/knowledge-sources") {
					return new Response(JSON.stringify(namespaces), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}
				return new Response(JSON.stringify({}), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			},
		);
	});

	it("renders compact document rows with pagination", async () => {
		renderWithProviders(<Knowledge />);

		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: "Knowledge" }),
		).toBeInTheDocument();

		await waitFor(() => {
			expect(
				screen.getByRole("list", { name: "Knowledge documents" }),
			).toBeInTheDocument();
		});

		const firstRecord = screen.getByRole("article", {
			name: /support-01/i,
		});
		expect(within(firstRecord).getByText("support-01")).toBeInTheDocument();
		expect(within(firstRecord).getByText("support")).toBeInTheDocument();
		expect(within(firstRecord).getByText("Global")).toBeInTheDocument();
		expect(
			within(firstRecord).getByText(/Support summary 01/),
		).toBeInTheDocument();
		expect(
			within(firstRecord).getByText(
				new Date("2026-08-01T12:00:00Z").toLocaleDateString(),
			),
		).toBeInTheDocument();

		expect(
			screen.getByRole("navigation", { name: /pagination/i }),
		).toBeInTheDocument();
	});

	it("selects all documents in selection mode", async () => {
		const { user } = renderWithProviders(<Knowledge />);
		await screen.findByRole("button", { name: "support-01" });
		await user.click(screen.getByRole("switch", { name: "Select" }));
		const selectAllButton = screen.getByRole("button", {
			name: /select all/i,
		});
		await user.click(selectAllButton);
		expect(screen.getByText("50 selected")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /clear all/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "support-01" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(
			screen.getByRole("button", { name: "sales-02" }),
		).toHaveAttribute("aria-pressed", "true");
	});

	it("opens delete confirmation from the overflow menu without losing primary open access", async () => {
		const { user } = renderWithProviders(<Knowledge />);
		await waitFor(() => {
			expect(
				screen.getByRole("list", { name: "Knowledge documents" }),
			).toBeInTheDocument();
		});

		await user.click(
			screen.getByRole("button", {
				name: /more actions for support-01/i,
			}),
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		expect(
			screen.getByRole("alertdialog", { name: /delete document/i }),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Cancel" }));

		await user.click(
			within(
				screen.getByRole("article", { name: /support-01/i }),
			).getByRole("button", { name: /support-01/i }),
		);
		expect(await screen.findByRole("dialog")).toHaveTextContent(
			"Knowledge drawer support doc-01",
		);
	});

	it("keeps rows visible and shows refresh progress only in the toolbar", async () => {
		const { user } = renderWithProviders(<Knowledge />);
		await screen.findByRole("button", { name: "support-01" });
		let finish!: (response: Response) => void;
		mockAuthFetch.mockImplementation((url: string) =>
			url.includes("/documents")
				? new Promise<Response>((resolve) => {
						finish = resolve;
					})
				: Promise.resolve(new Response(JSON.stringify(namespaces))),
		);
		await user.click(screen.getByRole("button", { name: "Refresh" }));
		expect(screen.getByRole("button", { name: "Refresh" })).toHaveAttribute(
			"aria-busy",
			"true",
		);
		expect(
			screen.getByRole("button", { name: "support-01" }),
		).toBeVisible();
		expect(
			screen.queryByText(/Updating documents/),
		).not.toBeInTheDocument();
		await act(async () => finish(new Response(JSON.stringify(makeDocs()))));
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Refresh" }),
			).toHaveAttribute("aria-busy", "false"),
		);
	});

	it("searches namespaces and filters documents using the selected namespace", async () => {
		const { user } = renderWithProviders(<Knowledge />);
		await screen.findByRole("button", { name: "support-01" });
		await user.click(screen.getByRole("button", { name: "Filters" }));
		await user.click(
			screen.getByRole("combobox", { name: "Filter by namespace" }),
		);
		await user.type(
			screen.getByPlaceholderText("Search Namespaces..."),
			"supp",
		);
		expect(
			screen.queryByRole("option", { name: "sales" }),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("option", { name: "support" }));
		await waitFor(() =>
			expect(
				mockAuthFetch.mock.calls.some(([url]) =>
					String(url).includes("namespace=support"),
				),
			).toBe(true),
		);
	});

	it("surfaces HTTP delete failures and keeps the confirmation open for retry", async () => {
		let deleteAttempts = 0;
		mockAuthFetch.mockImplementation(
			async (input: unknown, options?: { method?: string }) => {
				const url =
					typeof input === "string"
						? input
						: input && typeof input === "object" && "url" in input
							? String((input as Request).url)
							: String(input);
				if (
					url.includes("/api/knowledge-sources/") &&
					url.includes("/documents/") &&
					options?.method === "DELETE"
				) {
					deleteAttempts += 1;
					if (deleteAttempts === 1) {
						return new Response(
							JSON.stringify({
								detail: "Synthetic document delete failure",
							}),
							{ status: 500 },
						);
					}
					return new Response(null, { status: 204 });
				}
				if (url.startsWith("/api/knowledge-sources/documents")) {
					return new Response(JSON.stringify(makeDocs()), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}
				if (url === "/api/knowledge-sources") {
					return new Response(JSON.stringify(namespaces), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}
				return new Response(JSON.stringify({}), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			},
		);

		const { user } = renderWithProviders(<Knowledge />);
		await waitFor(() => {
			expect(
				screen.getByRole("list", { name: "Knowledge documents" }),
			).toBeInTheDocument();
		});

		await user.click(
			screen.getByRole("button", {
				name: /more actions for support-01/i,
			}),
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		const dialog = screen.getByRole("alertdialog", {
			name: /delete document/i,
		});
		await user.click(screen.getByRole("button", { name: "Delete" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Synthetic document delete failure",
		);
		expect(dialog).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Retry deletion" }),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Retry deletion" }),
		);
		await waitFor(() =>
			expect(
				screen.queryByRole("alertdialog", { name: /delete document/i }),
			).not.toBeInTheDocument(),
		);
		expect(deleteAttempts).toBe(2);
	});

	it("shows HTTP failures without a false empty state and preserves selected records on refresh failure", async () => {
		let fail = true;
		mockAuthFetch.mockImplementation(
			async (url: string) =>
				new Response(
					JSON.stringify(
						url.includes("/documents")
							? fail
								? {}
								: makeDocs().slice(0, 1)
							: namespaces,
					),
					{
						status: url.includes("/documents") && fail ? 500 : 200,
					},
				),
		);
		const { user } = renderWithProviders(<Knowledge />);
		await screen.findByText("Documents could not be loaded");
		expect(
			screen.queryByText("No documents found"),
		).not.toBeInTheDocument();
		fail = false;
		await user.click(
			screen.getByRole("button", { name: "Retry documents" }),
		);
		await user.click(screen.getByRole("switch", { name: "Select" }));
		const checkbox = await screen.findByRole("button", {
			name: "support-01",
		});
		await user.click(checkbox);
		fail = true;
		await user.click(screen.getByRole("button", { name: "Refresh" }));
		await screen.findByText("Documents could not be loaded");
		expect(checkbox).toHaveAttribute("aria-pressed", "true");
		expect(
			screen.getByRole("article", { name: "support-01" }),
		).toBeVisible();
		fail = false;
		await user.click(
			screen.getByRole("button", { name: "Retry documents" }),
		);
		await waitFor(() =>
			expect(
				screen.queryByText("Documents could not be loaded"),
			).not.toBeInTheDocument(),
		);
	});

	it("discards an older search response after the current filter has loaded", async () => {
		let releaseOld: (response: Response) => void = () => {};
		let oldSignal: AbortSignal | undefined;
		mockAuthFetch.mockImplementation(
			async (url: string, options?: { signal: AbortSignal }) => {
				if (!url.includes("/documents"))
					return new Response(JSON.stringify(namespaces));
				if (
					new URL(url, "http://test").searchParams.get("search") ===
					"old"
				) {
					oldSignal = options?.signal;
					return new Promise<Response>((resolve) => {
						releaseOld = resolve;
					});
				}
				return new Response(
					JSON.stringify([
						{ ...makeDocs()[0], key: "Current document" },
					]),
				);
			},
		);
		const { user } = renderWithProviders(<Knowledge />);
		await screen.findByRole("article", { name: "Current document" });
		const search = screen.getByRole("textbox", {
			name: "Search documents",
		});
		await user.type(search, "old");
		await waitFor(() => expect(oldSignal).toBeDefined());
		await user.clear(search);
		await user.type(search, "new");
		await screen.findByRole("article", { name: "Current document" });
		expect(oldSignal?.aborted).toBe(true);
		await act(async () => {
			releaseOld(
				new Response(
					JSON.stringify([{ ...makeDocs()[0], key: "Old document" }]),
				),
			);
		});
		expect(
			screen.queryByRole("article", { name: "Old document" }),
		).not.toBeInTheDocument();
	});

	it("recovers unavailable namespace filters without hiding documents", async () => {
		let failNamespaces = true;
		mockAuthFetch.mockImplementation(
			async (url: string) =>
				new Response(
					JSON.stringify(
						url.includes("/documents")
							? makeDocs().slice(0, 1)
							: namespaces,
					),
					{
						status:
							!url.includes("/documents") && failNamespaces
								? 500
								: 200,
					},
				),
		);
		const { user } = renderWithProviders(<Knowledge />);
		await screen.findByText("Namespace filters could not be updated");
		expect(
			await screen.findByRole("article", { name: "support-01" }),
		).toBeVisible();
		failNamespaces = false;
		await user.click(
			screen.getByRole("button", { name: "Retry namespace filters" }),
		);
		await waitFor(() =>
			expect(
				screen.queryByText("Namespace filters could not be updated"),
			).not.toBeInTheDocument(),
		);
	});
});
