/**
 * Generic Monaco editor wrapper for JSON / YAML structured-document
 * fields. This component is intentionally dumb: it does NOT own state,
 * does NOT parse, and does NOT serialize. It just shows the given
 * `text` in Monaco with the right language and theming, and emits
 * `onChange` on every keystroke.
 *
 * Consumers (e.g. `PolicyEditor`, `TableDialog`'s schema field) own
 * the per-field text buffers and any parse/reserialize plumbing. The
 * `path` prop is passed through to Monaco so multiple editors can
 * coexist on the same page (Monaco models are keyed by path).
 *
 * Authoritative validation (where applicable) lives on the server;
 * this component is format-only (JSON / YAML syntax). No JSON Schema
 * is registered with Monaco.
 */

import Editor from "@monaco-editor/react";

import { useTheme } from "@/contexts/ThemeContext";

export interface CodeEditorProps {
	mode: "json" | "yaml";
	text: string;
	onChange: (next: string) => void;
	/** Monaco needs a unique path per mounted editor — used as `aria-label` in the test mock. */
	path: string;
	/** CSS height; defaults to 320px. */
	height?: string;
	/**
	 * Render the editor read-only. Monaco grays the cursor and disables
	 * typing automatically when this is set; consumers typically pass a
	 * no-op `onChange` since no edits will fire.
	 */
	readOnly?: boolean;
	"data-testid"?: string;
}

export function CodeEditor({
	mode,
	text,
	onChange,
	path,
	height = "320px",
	readOnly = false,
	"data-testid": testId,
}: CodeEditorProps) {
	const { theme } = useTheme();
	const monacoTheme = theme === "dark" ? "vs-dark" : "light";

	return (
		<div
			className="border rounded-md overflow-hidden"
			style={{ height }}
			data-testid={testId}
		>
			<Editor
				height="100%"
				language={mode}
				value={text}
				onChange={(next) => onChange(next ?? "")}
				theme={monacoTheme}
				path={path}
				options={{
					minimap: { enabled: false },
					scrollBeyondLastLine: false,
					fontSize: 12,
					fontFamily:
						"ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
					wordWrap: "on",
					automaticLayout: true,
					tabSize: 2,
					formatOnPaste: true,
					readOnly,
					// Read-only displays (e.g., reference-panel examples) don't
					// need the line-number / folding gutters — drop them to
					// reclaim left margin. Editable consumers keep both.
					lineNumbers: readOnly ? "off" : "on",
					folding: !readOnly,
					lineDecorationsWidth: readOnly ? 0 : undefined,
					lineNumbersMinChars: readOnly ? 0 : undefined,
				}}
			/>
		</div>
	);
}
