import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
	const editorWorker = vi.fn(function EditorWorker() {
		return { kind: "editor" };
	});
	const jsonWorker = vi.fn(function JsonWorker() {
		return { kind: "json" };
	});
	const cssWorker = vi.fn(function CssWorker() {
		return { kind: "css" };
	});
	const htmlWorker = vi.fn(function HtmlWorker() {
		return { kind: "html" };
	});
	const tsWorker = vi.fn(function TsWorker() {
		return { kind: "typescript" };
	});
	const typescriptDefaults = {
		setCompilerOptions: vi.fn(),
		setDiagnosticsOptions: vi.fn(),
	};
	const javascriptDefaults = {
		setCompilerOptions: vi.fn(),
		setDiagnosticsOptions: vi.fn(),
	};
	const languages = {
		getLanguages: vi.fn(() => [{ id: "python" }]),
		setLanguageConfiguration: vi.fn(),
		registerCodeLensProvider: vi.fn(),
	};
	const editor = { registerCommand: vi.fn() };

	return {
		loaderConfig: vi.fn(),
		monaco: {
			languages,
			editor,
			typescript: {
				typescriptDefaults,
				javascriptDefaults,
				ScriptTarget: { ESNext: 99 },
				ModuleResolutionKind: { NodeJs: 2 },
				ModuleKind: { ESNext: 99 },
				JsxEmit: { React: 1 },
			},
		},
		editorWorker,
		jsonWorker,
		cssWorker,
		htmlWorker,
		tsWorker,
		typescriptDefaults,
		javascriptDefaults,
		languages,
		editor,
	};
});

vi.mock("@monaco-editor/react", () => ({
	loader: { config: mocks.loaderConfig },
}));
vi.mock("monaco-editor", () => mocks.monaco);
vi.mock("monaco-editor/editor/editor.worker?worker", () => ({
	default: mocks.editorWorker,
}));
vi.mock("monaco-editor/language/json/json.worker?worker", () => ({
	default: mocks.jsonWorker,
}));
vi.mock("monaco-editor/language/css/css.worker?worker", () => ({
	default: mocks.cssWorker,
}));
vi.mock("monaco-editor/language/html/html.worker?worker", () => ({
	default: mocks.htmlWorker,
}));
vi.mock("monaco-editor/language/typescript/ts.worker?worker", () => ({
	default: mocks.tsWorker,
}));

import { configureMonaco, initializeMonaco } from "./monaco-setup";

const originalEnvironment = globalThis.MonacoEnvironment;

afterEach(() => {
	globalThis.MonacoEnvironment = originalEnvironment;
});

describe("configureMonaco", () => {
	it("supplies local ESM Monaco and routes language workers through Vite assets", () => {
		configureMonaco();

		expect(mocks.loaderConfig).toHaveBeenCalledWith({
			monaco: mocks.monaco,
		});
		const getWorker = globalThis.MonacoEnvironment?.getWorker;
		expect(getWorker).toBeDefined();

		expect(getWorker!("1", "json")).toEqual({ kind: "json" });
		expect(getWorker!("2", "css")).toEqual({ kind: "css" });
		expect(getWorker!("3", "scss")).toEqual({ kind: "css" });
		expect(getWorker!("4", "less")).toEqual({ kind: "css" });
		expect(getWorker!("5", "html")).toEqual({ kind: "html" });
		expect(getWorker!("6", "handlebars")).toEqual({ kind: "html" });
		expect(getWorker!("7", "razor")).toEqual({ kind: "html" });
		expect(getWorker!("8", "javascript")).toEqual({ kind: "typescript" });
		expect(getWorker!("9", "typescript")).toEqual({ kind: "typescript" });
		expect(getWorker!("10", "python")).toEqual({ kind: "editor" });
		expect(getWorker!("11", "yaml")).toEqual({ kind: "editor" });

		expect(mocks.jsonWorker).toHaveBeenCalledTimes(1);
		expect(mocks.cssWorker).toHaveBeenCalledTimes(3);
		expect(mocks.htmlWorker).toHaveBeenCalledTimes(3);
		expect(mocks.tsWorker).toHaveBeenCalledTimes(2);
		expect(mocks.editorWorker).toHaveBeenCalledTimes(2);

		const environment = globalThis.MonacoEnvironment;
		configureMonaco();
		expect(mocks.loaderConfig).toHaveBeenCalledTimes(1);
		expect(globalThis.MonacoEnvironment).toBe(environment);
	});
});

describe("initializeMonaco", () => {
	it("configures TypeScript through the ESM API before registering CodeLens", async () => {
		const monaco = await import("monaco-editor");
		await initializeMonaco(monaco);

		const compilerOptions = {
			target: 99,
			allowNonTsExtensions: true,
			moduleResolution: 2,
			module: 99,
			noEmit: true,
			esModuleInterop: true,
			jsx: 1,
			reactNamespace: "React",
			allowJs: true,
			strict: false,
			skipLibCheck: true,
		};
		expect(mocks.typescriptDefaults.setCompilerOptions).toHaveBeenCalledWith(
			compilerOptions,
		);
		expect(mocks.javascriptDefaults.setCompilerOptions).toHaveBeenCalledWith(
			compilerOptions,
		);
		expect(mocks.typescriptDefaults.setDiagnosticsOptions).toHaveBeenCalledWith(
			{ noSemanticValidation: true, noSyntaxValidation: false },
		);
		expect(mocks.javascriptDefaults.setDiagnosticsOptions).toHaveBeenCalledWith(
			{ noSemanticValidation: true, noSyntaxValidation: false },
		);
		expect(mocks.languages.registerCodeLensProvider).toHaveBeenCalledWith(
			"python",
			expect.any(Object),
		);
		expect(
			mocks.languages.registerCodeLensProvider.mock.invocationCallOrder[0],
		).toBeGreaterThan(
			mocks.javascriptDefaults.setDiagnosticsOptions.mock.invocationCallOrder[0],
		);
	});
});
