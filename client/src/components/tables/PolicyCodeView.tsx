/**
 * Single Monaco editor for the whole `TablePolicies` document, in either
 * JSON or YAML mode. This component is intentionally dumb: it does NOT
 * own state, does NOT parse, and does NOT serialize. It just shows the
 * given `text` in Monaco with the right language and theming, and emits
 * `onChange` on every keystroke.
 *
 * The parent (`PolicyEditor`) owns the per-tab text buffers and the
 * parse/reserialize plumbing — see `PolicyEditor.tsx` for the
 * lastSynced / render-phase reset / tab-switch parse logic.
 *
 * JSON mode binds the `Expr` schema via `configureMonacoSchema` for
 * inline validation hints inside `when` clauses; see
 * `policy-monaco-schema.ts` for the scope caveat.
 *
 * YAML mode uses Monaco's built-in plain YAML language. There is no
 * schema binding — `monaco-yaml` is not in `package.json` — so YAML-side
 * validation comes entirely from the runtime parser in `PolicyEditor`.
 */

import Editor, { type OnMount } from "@monaco-editor/react";

import { useTheme } from "@/contexts/ThemeContext";

import { configureMonacoSchema } from "./policy-monaco-schema";

export interface PolicyCodeViewProps {
	mode: "json" | "yaml";
	text: string;
	onChange: (next: string) => void;
	onMount?: () => void;
	"data-testid"?: string;
}

export function PolicyCodeView({
	mode,
	text,
	onChange,
	onMount,
	"data-testid": testId,
}: PolicyCodeViewProps) {
	const { theme } = useTheme();
	const monacoTheme = theme === "dark" ? "vs-dark" : "light";

	const handleEditorMount: OnMount = (_editor, monaco) => {
		if (mode === "json") {
			configureMonacoSchema(monaco);
		}
		onMount?.();
	};

	return (
		<div
			className="border rounded-md overflow-hidden h-[320px]"
			data-testid={testId}
		>
			<Editor
				height="100%"
				language={mode}
				value={text}
				onChange={(next) => onChange(next ?? "")}
				onMount={handleEditorMount}
				theme={monacoTheme}
				path={`policies.${mode}`}
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
				}}
			/>
		</div>
	);
}
