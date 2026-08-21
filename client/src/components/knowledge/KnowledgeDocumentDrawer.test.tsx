import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { KnowledgeDocumentDrawer } from "./KnowledgeDocumentDrawer";

const mockAuthFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: ({
		content,
		onChange,
		readOnly,
	}: {
		content: string;
		onChange: (value: string) => void;
		readOnly?: boolean;
	}) => (
		<textarea
			aria-label="Document content"
			value={content}
			onChange={(event) => onChange(event.target.value)}
			readOnly={readOnly}
		/>
	),
}));

function documentResponse(organizationId: string | null) {
	return {
		ok: true,
		json: async () => ({
			id: "doc-1",
			namespace: "runbooks",
			key: "restart-service",
			content: "Restart the service.",
			metadata: {},
			organization_id: organizationId,
			created_at: "2026-08-20T12:00:00Z",
			updated_at: "2026-08-20T12:00:00Z",
		}),
	};
}

beforeEach(() => {
	vi.clearAllMocks();
});

describe("KnowledgeDocumentDrawer", () => {
	it("shows inherited Global knowledge read-only in an Organization context", async () => {
		mockAuthFetch.mockResolvedValue(documentResponse(null));

		renderWithProviders(
			<KnowledgeDocumentDrawer
				namespace="runbooks"
				documentId="doc-1"
				isCreating={false}
				canEdit
				selectedOrganizationId="org-1"
				onClose={vi.fn()}
			/>,
		);

		const editor = await screen.findByRole("textbox", {
			name: /document content/i,
		});
		expect(editor).toHaveAttribute("readonly");
		expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
		expect(screen.getAllByRole("button", { name: /close/i })).toHaveLength(2);
	});

	it("updates an exact-boundary document without a second scope selector", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(documentResponse("org-1"))
			.mockResolvedValueOnce({ ok: true, json: async () => ({}) });
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<KnowledgeDocumentDrawer
				namespace="runbooks"
				documentId="doc-1"
				isCreating={false}
				canEdit
				selectedOrganizationId="org-1"
				onClose={onClose}
			/>,
		);

		const editor = await screen.findByRole("textbox", {
			name: /document content/i,
		});
		await user.clear(editor);
		await user.type(editor, "Restart and verify the service.");
		await user.click(screen.getByRole("button", { name: /save/i }));

		await waitFor(() =>
			expect(mockAuthFetch).toHaveBeenLastCalledWith(
				"/api/knowledge-sources/runbooks/documents/doc-1",
				expect.objectContaining({ method: "PUT" }),
			),
		);
		expect(onClose).toHaveBeenCalled();
	});
});
