/**
 * Component tests for the shared JsonYamlEditor.
 *
 * The editor is a two-tab shell (JSON / YAML). Each tab renders a single
 * Monaco editor for the whole document. This test file mocks
 * `@monaco-editor/react` to a textarea labelled by its `path` prop so
 * we can drive the buffers from tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";

vi.mock("@monaco-editor/react", () => ({
	default: ({
		value,
		onChange,
		path,
	}: {
		value?: string;
		onChange?: (v: string | undefined) => void;
		path?: string;
	}) => (
		<textarea
			aria-label={path ?? "monaco-editor"}
			value={value ?? ""}
			onChange={(e) => onChange?.(e.target.value)}
		/>
	),
}));

vi.mock("@/contexts/ThemeContext", () => ({
	useTheme: () => ({ theme: "light" }),
}));

import { JsonYamlEditor } from "./JsonYamlEditor";

interface Doc {
	policies: { name: string }[];
}

const schema = {
	type: "object",
	properties: {
		policies: {
			type: "array",
			items: {
				type: "object",
				properties: { name: { type: "string" } },
				required: ["name"],
			},
		},
	},
	required: ["policies"],
};

let onChange: ReturnType<typeof vi.fn<(next: Doc | null) => void>>;

beforeEach(() => {
	onChange = vi.fn<(next: Doc | null) => void>();
});

function lastEmitted(): Doc | null {
	return onChange.mock.calls.at(-1)?.[0] as Doc | null;
}

describe("JsonYamlEditor", () => {
	it("default-renders the JSON view with the JSON tab selected", () => {
		renderWithProviders(
			<JsonYamlEditor<Doc>
				value={null}
				onChange={onChange}
				schema={schema}
			/>,
		);
		const jsonTab = screen.getByRole("tab", { name: /json/i });
		expect(jsonTab).toHaveAttribute("data-state", "active");
		expect(screen.getByLabelText("document.json")).toBeVisible();
	});

	it("respects defaultFormat=yaml", async () => {
		const { user } = renderWithProviders(
			<JsonYamlEditor<Doc>
				value={null}
				onChange={onChange}
				schema={schema}
				defaultFormat="yaml"
			/>,
		);
		// Avoid unused-var on user
		void user;
		const yamlTab = screen.getByRole("tab", { name: /yaml/i });
		expect(yamlTab).toHaveAttribute("data-state", "active");
		expect(screen.getByLabelText("document.yaml")).toBeVisible();
	});

	it("toggling to YAML changes the active tab", async () => {
		const { user } = renderWithProviders(
			<JsonYamlEditor<Doc>
				value={null}
				onChange={onChange}
				schema={schema}
			/>,
		);
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		expect(screen.getByLabelText("document.yaml")).toBeVisible();
	});

	it("calls onChange(null) when the buffer is cleared", () => {
		renderWithProviders(
			<JsonYamlEditor<Doc>
				value={{ policies: [{ name: "p1" }] }}
				onChange={onChange}
				schema={schema}
			/>,
		);
		const editor = screen.getByLabelText(
			"document.json",
		) as HTMLTextAreaElement;
		fireEvent.change(editor, { target: { value: "" } });
		expect(lastEmitted()).toBeNull();
	});

	it("calls onChange(parsed) when valid JSON is typed", () => {
		renderWithProviders(
			<JsonYamlEditor<Doc>
				value={null}
				onChange={onChange}
				schema={schema}
			/>,
		);
		const editor = screen.getByLabelText(
			"document.json",
		) as HTMLTextAreaElement;
		const next = JSON.stringify({ policies: [{ name: "p1" }] }, null, 2);
		fireEvent.change(editor, { target: { value: next } });
		const emitted = lastEmitted();
		expect(emitted).not.toBeNull();
		expect(emitted!.policies).toHaveLength(1);
		expect(emitted!.policies[0]!.name).toBe("p1");
	});

	it("does NOT call onChange when invalid JSON is typed", () => {
		renderWithProviders(
			<JsonYamlEditor<Doc>
				value={null}
				onChange={onChange}
				schema={schema}
			/>,
		);
		const editor = screen.getByLabelText(
			"document.json",
		) as HTMLTextAreaElement;
		onChange.mockClear();
		fireEvent.change(editor, { target: { value: "{not json" } });
		expect(onChange).not.toHaveBeenCalled();
	});

	it("blocks tab switch while a parse error is unresolved", async () => {
		const { user } = renderWithProviders(
			<JsonYamlEditor<Doc>
				value={null}
				onChange={onChange}
				schema={schema}
			/>,
		);
		const editor = screen.getByLabelText(
			"document.json",
		) as HTMLTextAreaElement;
		fireEvent.change(editor, { target: { value: "{not json" } });
		// Try to switch to YAML. The parse-error row stays, and the JSON
		// editor remains visible (i.e. activeTab did not change).
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		expect(
			screen.getByTestId("json-yaml-editor-parse-error"),
		).toBeInTheDocument();
		expect(screen.getByLabelText("document.json")).toBeVisible();
		const jsonTab = screen.getByRole("tab", { name: /json/i });
		expect(jsonTab).toHaveAttribute("data-state", "active");
	});

	it("fires onParseErrorChange with the error and then null as the buffer recovers", () => {
		const onParseErrorChange = vi.fn<(error: string | null) => void>();
		renderWithProviders(
			<JsonYamlEditor<Doc>
				value={null}
				onChange={onChange}
				schema={schema}
				onParseErrorChange={onParseErrorChange}
			/>,
		);
		const editor = screen.getByLabelText(
			"document.json",
		) as HTMLTextAreaElement;
		// Type invalid JSON — spy should be called with a non-null error.
		fireEvent.change(editor, { target: { value: "{not json" } });
		const errorCalls = onParseErrorChange.mock.calls.filter(
			(call) => call[0] !== null,
		);
		expect(errorCalls.length).toBeGreaterThan(0);
		expect(errorCalls.at(-1)![0]).toBeTruthy();

		// Type valid JSON — spy should be called with null.
		const next = JSON.stringify({ policies: [{ name: "p1" }] }, null, 2);
		fireEvent.change(editor, { target: { value: next } });
		expect(onParseErrorChange).toHaveBeenLastCalledWith(null);
	});

	it("seeds the JSON buffer from the `seed` prop when value is null", () => {
		const seed: Doc = { policies: [] };
		renderWithProviders(
			<JsonYamlEditor<Doc>
				value={null}
				onChange={onChange}
				schema={schema}
				seed={seed}
			/>,
		);
		const editor = screen.getByLabelText(
			"document.json",
		) as HTMLTextAreaElement;
		expect(editor.value).toBe(JSON.stringify(seed, null, 2));
	});
});
