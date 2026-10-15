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

	return {
		loaderConfig: vi.fn(),
		monaco: {},
		editorWorker,
		jsonWorker,
		cssWorker,
		htmlWorker,
		tsWorker,
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

import { configureMonaco } from "./monaco-setup";

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
