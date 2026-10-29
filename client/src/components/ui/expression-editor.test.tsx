import { render } from "@testing-library/react";
import type { OnMount } from "@monaco-editor/react";
import { expect, it, vi } from "vitest";
import { ExpressionEditor } from "./expression-editor";

const mocks = vi.hoisted(() => ({
	onMount: undefined as OnMount | undefined,
	appearanceOnMount: vi.fn(),
	setDiagnosticsOptions: vi.fn(),
	setCompilerOptions: vi.fn(),
	addExtraLib: vi.fn(),
}));

vi.mock("@monaco-editor/react", () => ({
	default: ({ onMount }: { onMount: OnMount }) => {
		mocks.onMount = onMount;
		return <div>Expression editor</div>;
	},
}));

vi.mock("monaco-editor", () => ({
	typescript: {
		ScriptTarget: { ES2020: 7 },
		javascriptDefaults: {
			setDiagnosticsOptions: mocks.setDiagnosticsOptions,
			setCompilerOptions: mocks.setCompilerOptions,
			addExtraLib: mocks.addExtraLib,
		},
	},
}));

vi.mock("@/hooks/useBifrostMonacoTheme", () => ({
	useBifrostMonacoTheme: () => ({
		theme: "test",
		options: {},
		onMount: mocks.appearanceOnMount,
	}),
}));

it("configures JavaScript validation and form context using Monaco's exported language API", () => {
	render(<ExpressionEditor value="context.field.enabled" onChange={vi.fn()} />);
	const editor = { onDidBlurEditorText: vi.fn() };
	const monaco = { languages: {} };

	// The bundled Monaco API has no languages.typescript compatibility namespace.
	mocks.onMount!(
		editor as unknown as Parameters<OnMount>[0],
		monaco as unknown as Parameters<OnMount>[1],
	);

	expect(mocks.appearanceOnMount).toHaveBeenCalledWith(editor, monaco);
	expect(mocks.setDiagnosticsOptions).toHaveBeenCalledWith({
		noSemanticValidation: false,
		noSyntaxValidation: false,
	});
	expect(mocks.setCompilerOptions).toHaveBeenCalledWith({
		target: 7,
		allowNonTsExtensions: true,
	});
	expect(mocks.addExtraLib).toHaveBeenCalledWith(
		expect.stringContaining("declare const context:"),
		"ts:context.d.ts",
	);
	const [declarations] = mocks.addExtraLib.mock.calls[0];
	expect(declarations).toContain("workflow: Record<string, any>");
	expect(declarations).toContain("query: Record<string, string>");
	expect(declarations).toContain("field: Record<string, any>");
});
