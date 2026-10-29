import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderWithProviders, screen, waitFor } from "@/test-utils";
import { KnowledgeDocumentDrawer } from "./KnowledgeDocumentDrawer";
const fetchMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => fetchMock(...args),
}));
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		isPlatformAdmin: true,
		user: { organizationId: "org-1" },
	}),
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAnywhere: () => true,
		canAt: () => true,
	}),
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		id,
		disabled,
	}: {
		id: string;
		disabled: boolean;
	}) => (
		<select id={id} disabled={disabled}>
			<option>Global</option>
		</select>
	),
}));
vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: ({
		content,
		onChange,
		readOnly,
		ariaLabel,
	}: {
		content: string;
		onChange: (value: string) => void;
		readOnly: boolean;
		ariaLabel: string;
	}) => (
		<textarea
			aria-label={ariaLabel}
			value={content}
			readOnly={readOnly}
			onChange={(e) => onChange(e.target.value)}
		/>
	),
}));
const doc = {
	id: "doc-1",
	namespace: "support",
	key: "Support guidance",
	content: "Original guidance",
	metadata: { revision: 2 },
	organization_id: null,
};
const response = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status });
const props = {
	namespace: "support",
	documentId: "doc-1",
	isCreating: false,
	onClose: vi.fn(),
};
beforeEach(() => {
	vi.clearAllMocks();
	fetchMock.mockResolvedValue(response(doc));
});

describe("KnowledgeDocumentDrawer", () => {
	it("retains edits after failed save, blocks dismissal while pending, and retries the same metadata", async () => {
		let release: (value: Response) => void = () => {};
		let saves = 0;
		fetchMock.mockImplementation(
			async (_url: string, options?: RequestInit) => {
				if (options?.method !== "PUT") return response(doc);
				saves++;
				if (saves === 1)
					return new Promise<Response>((resolve) => {
						release = resolve;
					});
				return response(doc);
			},
		);
		const { user } = renderWithProviders(
			<KnowledgeDocumentDrawer {...props} />,
		);
		const editor = await screen.findByRole("textbox", {
			name: "Document content",
		});
		await user.clear(editor);
		await user.type(editor, "Updated guidance");
		await user.click(screen.getByRole("button", { name: "Save" }));
		expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
		expect(editor).toHaveAttribute("readonly");
		await user.keyboard("{Escape}");
		expect(props.onClose).not.toHaveBeenCalled();
		await act(async () =>
			release(
				response({ detail: { message: "Try again shortly" } }, 500),
			),
		);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Try again shortly",
		);
		expect(editor).toHaveValue("Updated guidance");
		await user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(props.onClose).toHaveBeenCalledOnce());
		const writes = fetchMock.mock.calls.filter(
			([, options]) => options?.method === "PUT",
		);
		expect(writes).toHaveLength(2);
		expect(writes[0][0]).toBe(
			"/api/knowledge-sources/support/documents/doc-1?scope=global",
		);
		expect(JSON.parse(writes[0][1].body)).toEqual({
			content: "Updated guidance",
			metadata: doc.metadata,
		});
		expect(writes[1][1].body).toBe(writes[0][1].body);
	});

	it("offers load retry and never enables saving a document that failed to load", async () => {
		fetchMock
			.mockResolvedValueOnce(response({}, 500))
			.mockResolvedValueOnce(response(doc));
		const { user } = renderWithProviders(
			<KnowledgeDocumentDrawer {...props} />,
		);
		await screen.findByText("Document could not be loaded");
		expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
		expect(
			screen.queryByRole("textbox", { name: "Document content" }),
		).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Retry document" }),
		);
		expect(
			await screen.findByRole("textbox", { name: "Document content" }),
		).toHaveValue(doc.content);
	});

	it("creates with namespace/key/scope and clears cancelled drafts on reopen", async () => {
		const create = { ...props, documentId: null, isCreating: true };
		const { user, rerender } = renderWithProviders(
			<KnowledgeDocumentDrawer {...create} />,
		);
		await user.type(screen.getByLabelText("Namespace"), "company docs");
		await user.type(screen.getByLabelText("Key (optional)"), "reference");
		await user.type(
			screen.getByRole("textbox", { name: "Document content" }),
			"New guidance",
		);
		await user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(props.onClose).toHaveBeenCalledOnce());
		expect(fetchMock).toHaveBeenCalledWith(
			"/api/knowledge-sources/company%20docs/documents?scope=org-1",
			expect.objectContaining({
				method: "POST",
				body: JSON.stringify({
					content: "New guidance",
					key: "reference",
					metadata: {},
				}),
			}),
		);
		rerender(<KnowledgeDocumentDrawer {...create} isCreating={false} />);
		rerender(<KnowledgeDocumentDrawer {...create} />);
		expect(screen.getByLabelText("Namespace")).toHaveValue("");
		expect(
			screen.getByRole("textbox", { name: "Document content" }),
		).toHaveValue("");
	});
	it("requires confirmation before replacing a conflicting document", async () => {
		fetchMock.mockImplementation(
			async (url: string, options?: RequestInit) => {
				if (options?.method !== "PUT" || url.includes("replace=true"))
					return response(doc);
				return response(
					{
						detail: {
							message:
								"A document already has this key in the target scope.",
						},
					},
					409,
				);
			},
		);
		const { user } = renderWithProviders(
			<KnowledgeDocumentDrawer {...props} />,
		);
		await screen.findByRole("textbox", { name: "Document content" });
		await user.click(screen.getByRole("button", { name: "Save" }));
		await screen.findByRole("alertdialog");
		expect(props.onClose).not.toHaveBeenCalled();
		expect(
			fetchMock.mock.calls.some(([url]) => url.includes("replace=true")),
		).toBe(false);
		await user.click(screen.getByRole("button", { name: "Replace" }));
		await waitFor(() => expect(props.onClose).toHaveBeenCalledOnce());
		expect(fetchMock).toHaveBeenCalledWith(
			"/api/knowledge-sources/support/documents/doc-1?scope=global&replace=true",
			expect.objectContaining({
				method: "PUT",
				body: JSON.stringify({
					content: doc.content,
					metadata: doc.metadata,
				}),
			}),
		);
	});

	it("renders embedded editing in the parent pane and reports save busy state", async () => {
		let release: (value: Response) => void = () => {};
		const onBusyChange = vi.fn();
		fetchMock.mockImplementation(
			async (_url: string, options?: RequestInit) => {
				if (options?.method !== "PUT") return response(doc);
				return new Promise<Response>((resolve) => {
					release = resolve;
				});
			},
		);
		const { user } = renderWithProviders(
			<KnowledgeDocumentDrawer
				{...props}
				embedded
				onBusyChange={onBusyChange}
			/>,
		);
		const editor = await screen.findByRole("textbox", {
			name: "Document content",
		});
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Close document" }),
		).toBeEnabled();
		await user.clear(editor);
		await user.type(editor, "Pane guidance");
		await user.click(screen.getByRole("button", { name: "Save" }));
		expect(
			screen.getByRole("button", { name: "Close document" }),
		).toBeDisabled();
		await waitFor(() => expect(onBusyChange).toHaveBeenCalledWith(true));
		await act(async () => release(response(doc)));
		await waitFor(() => expect(props.onClose).toHaveBeenCalledOnce());
		expect(onBusyChange).toHaveBeenLastCalledWith(false);
	});
});
