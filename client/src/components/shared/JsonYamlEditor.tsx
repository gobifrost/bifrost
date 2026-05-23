/**
 * Shared JSON/YAML Monaco editor with a tabbed shell.
 *
 * Two tabs (JSON / YAML) expose the same `T | null` document through two
 * grammars. The shell owns the per-tab text buffers and the parse /
 * reserialize plumbing so tabs can swap freely without losing in-progress
 * edits. Consumers pass the current value and an `onChange` setter.
 *
 * When `value === null`, the JSON/YAML buffers seed to the `seed` prop
 * (defaults to `{}`) so the user has a scaffold to paste into. Clearing
 * the buffer collapses to `onChange(null)`. Invalid JSON / YAML does NOT
 * call `onChange`; the parent's last-good value stays intact.
 *
 * The `schema` prop is accepted for future Monaco schema registration
 * (autocomplete + inline JSON Schema validation). The current Monaco
 * wrapper (`CodeEditor`) does not register schemas; the prop is reserved
 * so the API can pick it up without a breaking change.
 */

import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import yaml from "js-yaml";

import { CodeEditor } from "@/components/tables/CodeEditor";
import {
	Tabs,
	TabsContent,
	TabsList,
	TabsTrigger,
} from "@/components/ui/tabs";

export type JsonYamlFormat = "json" | "yaml";

export interface JsonYamlEditorProps<T> {
	value: T | null;
	onChange: (next: T | null) => void;
	/** JSON Schema fed to Monaco for autocomplete + validation. Reserved
	 *  for a future Monaco schema-registration step; not yet wired into
	 *  the underlying CodeEditor. */
	schema: object;
	defaultFormat?: JsonYamlFormat;
	/** When `value` is null, what to seed the buffer with so the user has
	 *  a scaffold to paste into. Defaults to `{}`. */
	seed?: T;
	/** Optional post-parse validator. Receives the JSON/YAML-parsed value
	 *  and must either return the (possibly narrowed) `T` or throw. A
	 *  thrown error is surfaced as a parse error — `onChange` is NOT
	 *  called. Use this to enforce document-shape invariants beyond
	 *  "is valid JSON / YAML" (e.g. "root must be `{policies: [...]}`"). */
	validateParsed?: (raw: unknown) => T;
	/** Monaco editor paths (also used as aria-labels in tests). Defaults
	 *  to `document.json` / `document.yaml`. Override when multiple
	 *  JsonYamlEditors coexist on the same page (Monaco models are keyed
	 *  by path) or when consumers want stable test labels. */
	paths?: { json?: string; yaml?: string };
	/** When true, the component does not render the parse-error row.
	 *  Use with `onParseErrorChange` so the consumer can render its own
	 *  error UI in the surrounding layout. */
	hideParseError?: boolean;
	/** Optional notification when the active tab's parse-error state
	 *  changes. Fires with the error message or `null` when the buffer
	 *  becomes valid (or is cleared). Used by consumers that need to
	 *  disable AST-driven mutations while a buffer is broken. */
	onParseErrorChange?: (error: string | null) => void;
	className?: string;
}

function serializeJson<T>(value: T | null, seed: T | object): string {
	return JSON.stringify(value ?? seed, null, 2);
}

function serializeYaml<T>(value: T | null, seed: T | object): string {
	return yaml.dump(value ?? seed);
}

export function JsonYamlEditor<T>({
	value,
	onChange,
	schema: _schema,
	defaultFormat = "json",
	seed,
	validateParsed,
	paths,
	hideParseError = false,
	onParseErrorChange,
	className,
}: JsonYamlEditorProps<T>): JSX.Element {
	// `_schema` accepted but not yet wired into Monaco. Read here once so
	// linters don't flag the unused prop while preserving the API contract.
	void _schema;

	// Stringify-as-memo-key: callers commonly pass `seed={{...}}` inline
	// without memoizing, so reference identity churns every render. We key
	// off the structural hash so `seedValue` only recomputes on actual
	// content change — otherwise the render-phase reset below would clobber
	// in-progress buffers every parent render.
	const seedKey = useMemo(() => JSON.stringify(seed ?? {}), [seed]);
	// eslint-disable-next-line react-hooks/exhaustive-deps -- seedKey is the structural-identity proxy for `seed`; reference is intentionally ignored
	const seedValue: T | object = useMemo(() => seed ?? {}, [seedKey]);
	const jsonPath = paths?.json ?? "document.json";
	const yamlPath = paths?.yaml ?? "document.yaml";

	const [activeTab, setActiveTab] = useState<JsonYamlFormat>(defaultFormat);

	// Per-tab text buffers. JSON/YAML keep their own text so a partial
	// edit isn't reverted to the canonical serialization on every keystroke.
	const [jsonText, setJsonText] = useState<string>(() =>
		serializeJson(value, seedValue),
	);
	const [yamlText, setYamlText] = useState<string>(() =>
		serializeYaml(value, seedValue),
	);
	const [jsonParseError, setJsonParseError] = useState<string | null>(null);
	const [yamlParseError, setYamlParseError] = useState<string | null>(null);

	// `lastSynced{Json,Yaml}` track the canonical text we either emitted or
	// last accepted from props. The render-phase reset below uses these to
	// distinguish "external value changed" from "we just echoed our own
	// commit back" (we don't want to clobber a mid-typed buffer in the
	// latter case).
	const [lastSyncedJson, setLastSyncedJson] = useState<string>(() =>
		serializeJson(value, seedValue),
	);
	const [lastSyncedYaml, setLastSyncedYaml] = useState<string>(() =>
		serializeYaml(value, seedValue),
	);

	const externalJson = useMemo(
		() => serializeJson(value, seedValue),
		[value, seedValue],
	);
	const externalYaml = useMemo(
		() => serializeYaml(value, seedValue),
		[value, seedValue],
	);

	if (externalJson !== lastSyncedJson && externalJson !== jsonText) {
		setLastSyncedJson(externalJson);
		setJsonText(externalJson);
		setJsonParseError(null);
	}
	if (externalYaml !== lastSyncedYaml && externalYaml !== yamlText) {
		setLastSyncedYaml(externalYaml);
		setYamlText(externalYaml);
		setYamlParseError(null);
	}

	function emit(next: T | null) {
		const nextJson = serializeJson(next, seedValue);
		const nextYaml = serializeYaml(next, seedValue);
		setLastSyncedJson(nextJson);
		setLastSyncedYaml(nextYaml);
		// Refresh sibling buffers so a tab switch shows the latest value.
		// Keystroke-driven commits skip the active tab's buffer so the
		// user's in-progress text isn't clobbered.
		if (activeTab !== "json") {
			setJsonText(nextJson);
			setJsonParseError(null);
		}
		if (activeTab !== "yaml") {
			setYamlText(nextYaml);
			setYamlParseError(null);
		}
		onChange(next);
	}

	function handleJsonText(next: string) {
		setJsonText(next);
		const trimmed = next.trim();
		if (!trimmed) {
			setJsonParseError(null);
			emit(null);
			return;
		}
		try {
			const raw = JSON.parse(next);
			const parsed = validateParsed
				? validateParsed(raw)
				: (raw as T);
			setJsonParseError(null);
			emit(parsed);
		} catch (err) {
			setJsonParseError(
				err instanceof Error ? err.message : "Invalid JSON",
			);
		}
	}

	function handleYamlText(next: string) {
		setYamlText(next);
		const trimmed = next.trim();
		if (!trimmed) {
			setYamlParseError(null);
			emit(null);
			return;
		}
		try {
			const raw = yaml.load(next, { schema: yaml.JSON_SCHEMA });
			let parsed: T | null;
			if (raw === null) {
				parsed = null;
			} else if (validateParsed) {
				parsed = validateParsed(raw);
			} else {
				parsed = raw as T;
			}
			setYamlParseError(null);
			emit(parsed);
		} catch (err) {
			setYamlParseError(
				err instanceof Error ? err.message : "Invalid YAML",
			);
		}
	}

	function handleTabChange(nextRaw: string) {
		const next = nextRaw as JsonYamlFormat;
		if (next === activeTab) return;

		// Leaving a code tab: parse its buffer first so we have a fresh
		// AST to feed the destination tab. If parsing fails, stay put.
		if (activeTab === "json") {
			const trimmed = jsonText.trim();
			let parsed: T | null;
			try {
				if (!trimmed) {
					parsed = null;
				} else {
					const raw = JSON.parse(jsonText);
					parsed = validateParsed
						? validateParsed(raw)
						: (raw as T);
				}
			} catch (err) {
				setJsonParseError(
					err instanceof Error ? err.message : "Invalid JSON",
				);
				return;
			}
			setJsonParseError(null);
			emit(parsed);
		} else if (activeTab === "yaml") {
			const trimmed = yamlText.trim();
			let parsed: T | null;
			try {
				const raw = trimmed
					? yaml.load(yamlText, { schema: yaml.JSON_SCHEMA })
					: null;
				if (raw === null) {
					parsed = null;
				} else if (validateParsed) {
					parsed = validateParsed(raw);
				} else {
					parsed = raw as T;
				}
			} catch (err) {
				setYamlParseError(
					err instanceof Error ? err.message : "Invalid YAML",
				);
				return;
			}
			setYamlParseError(null);
			emit(parsed);
		}

		setActiveTab(next);
	}

	const activeParseError =
		activeTab === "json" ? jsonParseError : yamlParseError;

	// Fire the parse-error notification when the active tab's error state
	// changes (including tab switches that change which buffer is active).
	const lastNotifiedError = useRef<string | null | undefined>(undefined);
	useEffect(() => {
		if (!onParseErrorChange) return;
		if (lastNotifiedError.current !== activeParseError) {
			lastNotifiedError.current = activeParseError;
			onParseErrorChange(activeParseError);
		}
		// `onParseErrorChange` intentionally omitted: callers (e.g. PolicyEditor)
		// recreate the callback every render, which would re-run this effect on
		// every render. The `lastNotifiedError` ref guard enforces correctness.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [activeParseError]);

	return (
		<div className={className}>
			<Tabs
				value={activeTab}
				onValueChange={handleTabChange}
				className="min-h-[320px]"
			>
				<TabsList>
					<TabsTrigger value="json">JSON</TabsTrigger>
					<TabsTrigger value="yaml">YAML</TabsTrigger>
				</TabsList>

				<TabsContent value="json" className="min-h-[320px]">
					<CodeEditor
						mode="json"
						text={jsonText}
						onChange={handleJsonText}
						path={jsonPath}
						data-testid="json-yaml-editor-json"
					/>
				</TabsContent>

				<TabsContent value="yaml" className="min-h-[320px]">
					<CodeEditor
						mode="yaml"
						text={yamlText}
						onChange={handleYamlText}
						path={yamlPath}
						data-testid="json-yaml-editor-yaml"
					/>
				</TabsContent>
			</Tabs>

			{!hideParseError && activeParseError && (
				<p
					className="text-xs text-destructive mt-2"
					role="alert"
					data-testid="json-yaml-editor-parse-error"
				>
					Parse error: {activeParseError}
				</p>
			)}
		</div>
	);
}
