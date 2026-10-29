import { useBifrostMonacoTheme } from "@/hooks/useBifrostMonacoTheme";
import { useRef, useState } from "react";
import Editor, { OnMount } from "@monaco-editor/react";
import { typescript } from "monaco-editor";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { AlertCircle } from "lucide-react";

export interface ExpressionEditorProps {
	value: string;
	onChange: (value: string) => void;
	label?: string;
	helpText?: string;
	className?: string;
	height?: number;
	exampleFieldName?: string;
	onValidationChange?: (isValid: boolean, error?: string) => void;
}

/**
 * Monaco-based expression editor with JavaScript syntax highlighting
 * Provides real-time validation and autocomplete for form context expressions
 */
export function ExpressionEditor({
	value,
	onChange,
	label,
	helpText,
	className,
	height,
	exampleFieldName = "field_name",
	onValidationChange,
}: ExpressionEditorProps) {
	const editorRef = useRef<unknown>(null);
	const monacoAppearance = useBifrostMonacoTheme();
	const [validationError, setValidationError] = useState<string | null>(null);

	// Calculate height based on content (minimum 120px for autocomplete visibility, then grows)
	const lineCount = (value || "").split("\n").length;
	const calculatedHeight =
		height ?? Math.max(120, Math.min(lineCount * 20 + 24, 300));

	const validateExpression = (
		expr: string,
	): { isValid: boolean; error?: string } => {
		if (!expr.trim()) {
			return { isValid: true }; // Empty is valid
		}

		try {
			// Validate JavaScript syntax by attempting to create function
			// SECURITY NOTE: Only used for validation, not execution. Form builders (admins) write these.

			new Function("context", `return !!(${expr})`);
			return { isValid: true };
		} catch (error) {
			const errorMsg =
				error instanceof Error ? error.message : "Invalid expression";
			return { isValid: false, error: errorMsg };
		}
	};

	const handleBlur = () => {
		const validation = validateExpression(value);
		setValidationError(validation.error || null);
		onValidationChange?.(validation.isValid, validation.error);
	};

	const handleEditorDidMount: OnMount = (editor, monaco) => {
		editorRef.current = editor;
		monacoAppearance.onMount(editor, monaco);

		// Add blur event listener
		editor.onDidBlurEditorText(() => {
			handleBlur();
		});

		// Configure JavaScript language features
		typescript.javascriptDefaults.setDiagnosticsOptions({
			noSemanticValidation: false,
			noSyntaxValidation: false,
		});

		typescript.javascriptDefaults.setCompilerOptions({
			target: typescript.ScriptTarget.ES2020,
			allowNonTsExtensions: true,
		});

		// Add type definitions for form context
		typescript.javascriptDefaults.addExtraLib(
			`
      declare const context: {
        workflow: Record<string, any>;
        query: Record<string, string>;
        field: Record<string, any>;
      };
      `,
			"ts:context.d.ts",
		);
	};

	const defaultHelpText = `Example: context.field.${exampleFieldName} !== null && context.field.${exampleFieldName} !== ""`;

	return (
		<div className={cn("space-y-2", className)}>
			{label && <Label>{label}</Label>}
			<div
				className={cn(
					"border rounded-md overflow-hidden",
					validationError && "border-destructive",
				)}
			>
				<div className="px-3">
					<Editor
						height={calculatedHeight}
						defaultLanguage="javascript"
						value={value || ""}
						onChange={(newValue) => {
							onChange(newValue || "");
							// Clear validation error on change
							if (validationError) {
								setValidationError(null);
							}
						}}
						onMount={handleEditorDidMount}
						theme={monacoAppearance.theme}
						beforeMount={monacoAppearance.beforeMount}
						options={{
							minimap: { enabled: false },
							lineNumbers: "off",
							glyphMargin: false,
							folding: false,
							lineDecorationsWidth: 0,
							lineNumbersMinChars: 0,
							scrollBeyondLastLine: false,
							wordWrap: "on",
							wrappingStrategy: "advanced",
							...monacoAppearance.options,
							ariaLabel: label || "Expression",

							padding: { top: 8, bottom: 8 },
							suggest: {
								showWords: false,
								showFields: true,
								showProperties: true,
							},
							quickSuggestions: {
								other: true,
								comments: false,
								strings: false,
							},
							parameterHints: {
								enabled: true,
							},
							tabSize: 2,
							insertSpaces: true,
						}}
					/>
				</div>
			</div>
			{validationError && (
				<div className="flex items-start gap-2 text-xs text-destructive">
					<AlertCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
					<span>{validationError}</span>
				</div>
			)}
			{!validationError && helpText && (
				<p className="text-xs text-muted-foreground">{helpText}</p>
			)}
			{!validationError && !helpText && (
				<p className="text-xs text-muted-foreground">
					{defaultHelpText}
				</p>
			)}
		</div>
	);
}

// Export validation function for use in parent components
export function validateExpression(expr: string): {
	isValid: boolean;
	error?: string;
} {
	if (!expr.trim()) {
		return { isValid: true };
	}

	try {
		// Validate JavaScript syntax
		// SECURITY NOTE: Only used for validation. Form builders (admins) write these expressions.

		new Function("context", `return !!(${expr})`);
		return { isValid: true };
	} catch (error) {
		const errorMsg =
			error instanceof Error ? error.message : "Invalid expression";
		return { isValid: false, error: errorMsg };
	}
}
