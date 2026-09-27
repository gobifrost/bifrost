/**
 * Monaco Editor Setup and Configuration
 * Ensures all language features (comments, formatting, etc.) are properly loaded
 */
import { loader } from "@monaco-editor/react";
import type * as Monaco from "monaco-editor";
import { useWorkflowsStore } from "@/stores/workflowsStore";

let setupComplete = false;
let monacoInitialized = false;

// Side-channel for current file path (Monaco doesn't know workspace paths)
let _currentFilePath: string | null = null;
export function setCurrentFilePath(path: string | null) {
	_currentFilePath = path;
}
export function getCurrentFilePath() {
	return _currentFilePath;
}

/**
 * Configure Monaco editor before it loads
 * This must be called before any editor instances are created
 */
export function configureMonaco() {
	if (setupComplete) return;

	// Configure Monaco loader to use CDN or local worker files
	loader.config({
		paths: {
			vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.54.0/min/vs",
		},
	});

	setupComplete = true;
}

/**
 * Initialize Monaco editor features after it loads
 * This is called when the first editor instance mounts
 */
export async function initializeMonaco(monaco: typeof Monaco) {
	if (monacoInitialized) return;
	monacoInitialized = true;

	// Ensure Python language configuration is loaded
	// Monaco should have this by default, but we'll verify
	const pythonLang = monaco.languages
		.getLanguages()
		.find((lang) => lang.id === "python");

	if (!pythonLang) {
		console.warn("Python language not found in Monaco");
		return;
	}

	// Register Python language configuration for comments
	// This ensures Cmd+/ works for Python files
	monaco.languages.setLanguageConfiguration("python", {
		comments: {
			lineComment: "#",
			blockComment: ['"""', '"""'],
		},
		brackets: [
			["{", "}"],
			["[", "]"],
			["(", ")"],
		],
		autoClosingPairs: [
			{ open: "{", close: "}" },
			{ open: "[", close: "]" },
			{ open: "(", close: ")" },
			{ open: '"', close: '"', notIn: ["string"] },
			{ open: "'", close: "'", notIn: ["string", "comment"] },
		],
		surroundingPairs: [
			{ open: "{", close: "}" },
			{ open: "[", close: "]" },
			{ open: "(", close: ")" },
			{ open: '"', close: '"' },
			{ open: "'", close: "'" },
		],
		folding: {
			offSide: true,
			markers: {
				start: new RegExp("^\\s*#region\\b"),
				end: new RegExp("^\\s*#endregion\\b"),
			},
		},
	});

	// Configure JavaScript/TypeScript
	monaco.languages.setLanguageConfiguration("javascript", {
		comments: {
			lineComment: "//",
			blockComment: ["/*", "*/"],
		},
	});

	monaco.languages.setLanguageConfiguration("typescript", {
		comments: {
			lineComment: "//",
			blockComment: ["/*", "*/"],
		},
	});

	// Configure TypeScript/JavaScript compiler options to support JSX/TSX
	// Use jsx: React (1) for classic transform which doesn't require jsx runtime import
	// Note: Using type assertion as monaco.languages.typescript types are marked deprecated
	// but the runtime API still works
	const ts = monaco.languages.typescript as typeof monaco.languages.typescript & {
		typescriptDefaults: {
			setCompilerOptions: (options: Record<string, unknown>) => void;
			setDiagnosticsOptions: (options: Record<string, unknown>) => void;
		};
		javascriptDefaults: {
			setCompilerOptions: (options: Record<string, unknown>) => void;
			setDiagnosticsOptions: (options: Record<string, unknown>) => void;
		};
		ScriptTarget: { ESNext: number };
		ModuleResolutionKind: { NodeJs: number };
		ModuleKind: { ESNext: number };
		JsxEmit: { React: number };
	};

	ts.typescriptDefaults.setCompilerOptions({
		target: ts.ScriptTarget.ESNext,
		allowNonTsExtensions: true,
		moduleResolution: ts.ModuleResolutionKind.NodeJs,
		module: ts.ModuleKind.ESNext,
		noEmit: true,
		esModuleInterop: true,
		jsx: ts.JsxEmit.React,
		reactNamespace: "React",
		allowJs: true,
		strict: false, // Less strict for user code
		skipLibCheck: true,
	});

	ts.javascriptDefaults.setCompilerOptions({
		target: ts.ScriptTarget.ESNext,
		allowNonTsExtensions: true,
		moduleResolution: ts.ModuleResolutionKind.NodeJs,
		module: ts.ModuleKind.ESNext,
		noEmit: true,
		esModuleInterop: true,
		jsx: ts.JsxEmit.React,
		reactNamespace: "React",
		allowJs: true,
		strict: false,
		skipLibCheck: true,
	});

	// Disable semantic validation entirely for app code - we use Babel for compilation
	// and the platform scope provides runtime APIs that TypeScript doesn't know about
	ts.typescriptDefaults.setDiagnosticsOptions({
		noSemanticValidation: true,
		noSyntaxValidation: false,
	});

	ts.javascriptDefaults.setDiagnosticsOptions({
		noSemanticValidation: true,
		noSyntaxValidation: false,
	});

	// Configure YAML
	monaco.languages.setLanguageConfiguration("yaml", {
		comments: {
			lineComment: "#",
		},
	});

	// Configure JSON (doesn't support comments but we'll configure brackets)
	monaco.languages.setLanguageConfiguration("json", {
		brackets: [
			["{", "}"],
			["[", "]"],
		],
		autoClosingPairs: [
			{ open: "{", close: "}" },
			{ open: "[", close: "]" },
			{ open: '"', close: '"' },
		],
	});

	// Register CodeLens commands for Bifrost decorator registration
	monaco.editor.registerCommand(
		"bifrost.registerDecorator",
		(_accessor, filePath: string, functionName: string) => {
			window.dispatchEvent(
				new CustomEvent("bifrost-register-decorator", {
					detail: { filePath, functionName },
				}),
			);
		},
	);

	monaco.editor.registerCommand("bifrost.noop", () => {
		// No-op for "Registered" labels
	});

	// CodeLens provider for Python files — shows Register/Registered on decorators
	const decoratorRegex = /^(\s*)@(workflow|tool|data_provider|service)\b/;
	const defRegex = /^(\s*)(?:async\s+)?def\s+(\w+)\s*\(/;

	monaco.languages.registerCodeLensProvider("python", {
		provideCodeLenses(model) {
			const filePath = getCurrentFilePath();
			if (!filePath) return { lenses: [], dispose() {} };

			const state = useWorkflowsStore.getState();
			const registeredFns = state.getRegisteredFunctions(filePath);

			// Build a function_name -> workflow lookup for org info
			const workflowsByFn = new Map<string, { organization_id?: string | null }>();
			for (const wf of state.workflows) {
				if (wf.relative_file_path === filePath && wf.function_name) {
					workflowsByFn.set(wf.function_name, wf);
				}
			}

			const lenses: Monaco.languages.CodeLens[] = [];
			const lineCount = model.getLineCount();

			for (let i = 1; i <= lineCount; i++) {
				const line = model.getLineContent(i);
				const decoratorMatch = line.match(decoratorRegex);
				if (!decoratorMatch) continue;

				const decoratorType = decoratorMatch[2];

				// Look ahead up to 5 lines for the function definition
				let functionName: string | null = null;
				for (
					let j = i + 1;
					j <= Math.min(i + 5, lineCount);
					j++
				) {
					const defLine = model.getLineContent(j);
					const defMatch = defLine.match(defRegex);
					if (defMatch) {
						functionName = defMatch[2];
						break;
					}
				}

				if (!functionName) continue;

				const isRegistered = registeredFns.has(functionName);

				if (isRegistered) {
					const wf = workflowsByFn.get(functionName);
					const scopeLabel = wf?.organization_id ? "Org-scoped" : "Global";
					lenses.push({
						range: {
							startLineNumber: i,
							startColumn: 1,
							endLineNumber: i,
							endColumn: 1,
						},
						command: {
							id: "bifrost.noop",
							title: `$(check) Registered ${decoratorType} (${scopeLabel})`,
						},
					});
				} else {
					lenses.push({
						range: {
							startLineNumber: i,
							startColumn: 1,
							endLineNumber: i,
							endColumn: 1,
						},
						command: {
							id: "bifrost.registerDecorator",
							title: `$(play) Register ${decoratorType}`,
							arguments: [filePath, functionName],
						},
					});
				}
			}

			return { lenses, dispose() {} };
		},
		resolveCodeLens(_model, codeLens) {
			return codeLens;
		},
	});
}
