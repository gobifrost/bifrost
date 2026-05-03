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
 * Authoritative validation lives on the server; this component is
 * format-only (JSON / YAML syntax). No JSON Schema is registered with
 * Monaco — the previous `Expr`-only schema bound to `*` filematch was
 * misleading and produced false positives at the document level.
 */

import Editor from "@monaco-editor/react";

import { useTheme } from "@/contexts/ThemeContext";

export interface PolicyCodeViewProps {
	mode: "json" | "yaml";
	text: string;
	onChange: (next: string) => void;
	"data-testid"?: string;
}

export function PolicyCodeView({
	mode,
	text,
	onChange,
	"data-testid": testId,
}: PolicyCodeViewProps) {
	const { theme } = useTheme();
	const monacoTheme = theme === "dark" ? "vs-dark" : "light";

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
