import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import { CodeEditor } from "./CodeEditor";

vi.mock("@/hooks/useBifrostMonacoTheme", () => ({
	useBifrostMonacoTheme: () => ({ theme: "bifrost-dark", options: {} }),
}));
vi.mock("@monaco-editor/react", () => ({
	default: ({
		value,
		onChange,
		options,
	}: {
		value: string;
		onChange: (value: string) => void;
		options: { ariaLabel: string; readOnly: boolean };
	}) => (
		<textarea
			aria-label={options.ariaLabel}
			value={value}
			readOnly={options.readOnly}
			onChange={(event) => onChange(event.target.value)}
		/>
	),
}));

describe("CodeEditor", () => {
	it("exposes a readable field label independently of its unique model path", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<CodeEditor
				mode="json"
				path="unique-model.json"
				ariaLabel="Payload (dict)"
				text="{}"
				onChange={onChange}
			/>,
		);
		fireEvent.change(
			screen.getByRole("textbox", { name: "Payload (dict)" }),
			{ target: { value: '{"ok":true}' } },
		);
		expect(onChange).toHaveBeenCalledWith('{"ok":true}');
	});
	it("preserves existing path labels and read-only behavior", () => {
		renderWithProviders(
			<CodeEditor
				mode="json"
				path="document.json"
				text="{}"
				onChange={vi.fn()}
				readOnly
			/>,
		);
		expect(
			screen.getByRole("textbox", { name: "document.json" }),
		).toHaveAttribute("readonly");
	});
});
