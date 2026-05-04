/**
 * Component tests for DocumentDialog.
 *
 * Monaco is huge and can't run in happy-dom, so we stub @monaco-editor/react
 * to a plain textarea wired to value/onChange. That lets us exercise the real
 * submit / validation behaviour without a browser.
 *
 * Covers:
 * - create-mode: valid JSON → insertDocument with parsed data
 * - edit-mode: pre-fills from document.data → updateDocument with doc_id
 * - invalid JSON: shows error alert, Save button is disabled
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";

const mockInsertMutate = vi.fn();
const mockUpdateMutate = vi.fn();

vi.mock("@/services/tables", () => ({
	useInsertDocument: () => ({
		mutateAsync: mockInsertMutate,
		isPending: false,
	}),
	useUpdateDocument: () => ({
		mutateAsync: mockUpdateMutate,
		isPending: false,
	}),
}));

vi.mock("@/contexts/ThemeContext", () => ({
	useTheme: () => ({ theme: "light" }),
}));

// Monaco: stub to a textarea so we can drive value changes from tests.
vi.mock("@monaco-editor/react", () => ({
	default: ({
		value,
		onChange,
	}: {
		value?: string;
		onChange?: (v: string | undefined) => void;
	}) => (
		<textarea
			aria-label="document-json"
			value={value ?? ""}
			onChange={(e) => onChange?.(e.target.value)}
		/>
	),
}));

import { DocumentDialog } from "./DocumentDialog";

beforeEach(() => {
	mockInsertMutate.mockReset();
	mockInsertMutate.mockResolvedValue({});
	mockUpdateMutate.mockReset();
	mockUpdateMutate.mockResolvedValue({});
});

describe("DocumentDialog — create mode", () => {
	it("parses the JSON and calls insertDocument with the table_id", async () => {
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<DocumentDialog
				tableId="tbl-1"
				open={true}
				onClose={onClose}
			/>,
		);

		const editor = screen.getByLabelText(/document-json/i);
		fireEvent.change(editor, {
			target: { value: '{"foo": "bar"}' },
		});

		await user.click(screen.getByRole("button", { name: /^create$/i }));

		await waitFor(() => expect(mockInsertMutate).toHaveBeenCalled());
		expect(mockInsertMutate.mock.calls[0]![0]).toEqual({
			params: { path: { table_id: "tbl-1" } },
			body: { data: { foo: "bar" }, upsert: false },
		});
		expect(onClose).toHaveBeenCalled();
	});
});

describe("DocumentDialog — edit mode", () => {
	it("pre-fills from document.data and calls updateDocument with doc_id", async () => {
		const onClose = vi.fn();
		const doc = {
			id: "doc-1",
			data: { hello: "world" },
			created_at: "2026-04-20T00:00:00Z",
			updated_at: "2026-04-20T00:00:00Z",
		};
		const { user } = renderWithProviders(
			<DocumentDialog
				document={
					doc as unknown as Parameters<
						typeof DocumentDialog
					>[0]["document"]
				}
				tableId="tbl-1"
				open={true}
				onClose={onClose}
			/>,
		);

		const editor = screen.getByLabelText(/document-json/i) as HTMLTextAreaElement;
		expect(editor.value).toContain('"hello": "world"');

		await user.click(screen.getByRole("button", { name: /^update$/i }));

		await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalled());
		expect(mockUpdateMutate.mock.calls[0]![0]).toEqual({
			params: { path: { table_id: "tbl-1", doc_id: "doc-1" } },
			body: { data: { hello: "world" } },
		});
		expect(onClose).toHaveBeenCalled();
	});
});

describe("DocumentDialog — invalid JSON", () => {
	it("shows an error alert and disables the save button on invalid JSON", () => {
		renderWithProviders(
			<DocumentDialog
				tableId="tbl-1"
				open={true}
				onClose={vi.fn()}
			/>,
		);

		const editor = screen.getByLabelText(/document-json/i);
		fireEvent.change(editor, { target: { value: "{not valid" } });

		expect(screen.getByRole("alert")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /^create$/i })).toBeDisabled();
	});
});
