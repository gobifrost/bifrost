/**
 * Top-level policy editor — a tabbed shell that exposes the same
 * `TablePolicies | null` AST through two views:
 *   - JSON: Monaco editor of the whole TablePolicies document
 *   - YAML: plain Monaco YAML mode
 *
 * The shell owns the per-tab text buffers and the parse/reserialize
 * plumbing so tabs can swap freely without losing in-progress edits.
 * The parent (e.g. `TableDialog`) passes the current `TablePolicies | null`
 * and a setter, and is responsible for shipping the resulting structure
 * in the create/update request body.
 *
 * When `value === null`, the JSON/YAML buffers seed to a `{policies: []}`
 * wrapper so the user has a wrapper to paste into without manual editing.
 * The empty-collapse path (user clears the buffer → `onChange(null)`) is
 * handled separately on the keystroke handlers.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import yaml from "js-yaml";

import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	Tabs,
	TabsContent,
	TabsList,
	TabsTrigger,
} from "@/components/ui/tabs";

import { CodeEditor } from "./CodeEditor";
import { PolicyReferencePanel } from "./PolicyReferencePanel";
import {
	POLICY_TEMPLATES,
	instantiateTemplate,
	type Policy,
	type PolicyTemplateKey,
} from "./policy-templates";
import {
	validatePolicies,
	type PolicyValidationError,
} from "@/services/tables";
import type { components } from "@/lib/v1";

type TablePolicies = components["schemas"]["TablePolicies"];

/** Debounce window before calling the validator. Long enough to avoid
 *  hammering the endpoint while the user is mid-keystroke; short enough to
 *  feel "live" once they pause. */
const VALIDATE_DEBOUNCE_MS = 300;

type TabKey = "json" | "yaml";

export interface PolicyEditorProps {
	value: TablePolicies | null;
	onChange: (next: TablePolicies | null) => void;
}

/**
 * Canonical-text serializers. For `null` we emit the empty-wrapper form
 * (`{"policies": []}` / `policies: []`) so the user has a wrapper to paste
 * into without typing the scaffolding by hand. Empty buffers (user clears
 * the editor) collapse back to `onChange(null)` via the keystroke handler.
 */
function serializeJson(value: TablePolicies | null): string {
	return JSON.stringify(value ?? { policies: [] }, null, 2);
}

function serializeYaml(value: TablePolicies | null): string {
	return yaml.dump(value ?? { policies: [] });
}

/**
 * Validate that `parsed` is shaped like `TablePolicies` (i.e. an object
 * with a `policies` array). Returns the narrowed value or throws.
 */
function asTablePolicies(parsed: unknown): TablePolicies {
	if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("Document root must be an object with a `policies` key.");
	}
	const obj = parsed as Record<string, unknown>;
	if (!Array.isArray(obj.policies)) {
		throw new Error("`policies` must be an array.");
	}
	return parsed as TablePolicies;
}

export function PolicyEditor({ value, onChange }: PolicyEditorProps) {
	const [activeTab, setActiveTab] = useState<TabKey>("json");
	const [showRef, setShowRef] = useState(false);
	const [templateKey, setTemplateKey] = useState<string>("");

	// Per-tab text buffers. JSON/YAML keep their own text so a partial edit
	// isn't reverted to the canonical serialization on every keystroke.
	const [jsonText, setJsonText] = useState<string>(() => serializeJson(value));
	const [yamlText, setYamlText] = useState<string>(() => serializeYaml(value));
	const [jsonParseError, setJsonParseError] = useState<string | null>(null);
	const [yamlParseError, setYamlParseError] = useState<string | null>(null);

	// `lastSynced{Json,Yaml}` track the canonical text we either emitted or
	// last accepted from props. The render-phase reset below uses these to
	// distinguish "external value changed" from "we just echoed our own
	// commit back" (we don't want to clobber a mid-typed buffer in the
	// latter case) — i.e. a render-phase reset to keep external value in
	// sync without useEffect.
	const [lastSyncedJson, setLastSyncedJson] = useState<string>(() =>
		serializeJson(value),
	);
	const [lastSyncedYaml, setLastSyncedYaml] = useState<string>(() =>
		serializeYaml(value),
	);

	const externalJson = useMemo(() => serializeJson(value), [value]);
	const externalYaml = useMemo(() => serializeYaml(value), [value]);

	// `null` = haven't validated yet OR a parse error wiped any prior result.
	// `[]` = the server validated and found nothing wrong.
	// non-empty array = render these path/message rows below the parse-error.
	const [validationErrors, setValidationErrors] = useState<
		PolicyValidationError[] | null
	>(null);

	// Track the in-flight validation request so we can abort it (and ignore
	// late responses) when the user types again. Without this we'd race a
	// stale "INVALID" response against a fresh successful edit and flash
	// stale errors onto the page.
	const validateAbortRef = useRef<AbortController | null>(null);
	const lastValidatedJsonRef = useRef<string | null>(null);

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

	// Render-phase reset: when the AST clears (parent passes value=null,
	// keystroke handler emits(null), etc.) wipe any prior validation
	// results synchronously so the UI doesn't keep showing stale errors
	// for a buffer that no longer exists. Mirrors the `lastSynced*`
	// reset pattern above. The ref-clear lives in the effect below
	// (lint disallows ref mutation during render).
	if (value === null && validationErrors !== null) {
		setValidationErrors(null);
	}

	// Debounced server validation. The effect runs on every `value` change
	// (which only changes when a buffer parses successfully — `handleJson` /
	// `handleYaml` only call `emit` after a successful parse). On rapid
	// successive edits, abort the prior request so we don't race stale
	// responses into the UI.
	useEffect(() => {
		if (value === null) {
			lastValidatedJsonRef.current = null;
			return;
		}
		// Dedupe identical states: if we already validated this exact AST,
		// skip the round trip. Canonical-serialize for the cache key so two
		// structurally identical objects with different reference identity
		// hit the cache.
		const canonical = JSON.stringify(value);
		if (canonical === lastValidatedJsonRef.current) return;

		const controller = new AbortController();
		validateAbortRef.current?.abort();
		validateAbortRef.current = controller;

		const timer = setTimeout(() => {
			validatePolicies(value, { signal: controller.signal })
				.then((response) => {
					if (controller.signal.aborted) return;
					lastValidatedJsonRef.current = canonical;
					setValidationErrors(
						response.ok ? [] : response.errors ?? [],
					);
				})
				.catch((err) => {
					if (controller.signal.aborted) return;
					// Network / 5xx — don't render stale errors. The editor
					// degrades to "no validation feedback"; saving still
					// validates authoritatively at the create/update endpoint.
					if (err instanceof Error && err.name === "AbortError")
						return;
					setValidationErrors(null);
				});
		}, VALIDATE_DEBOUNCE_MS);

		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	}, [value]);


	function emit(
		next: TablePolicies | null,
		opts: { resyncBuffers?: boolean } = {},
	) {
		// Empty policy list collapses back to null so we don't persist
		// `{policies: []}` and accidentally lock the table down for everyone.
		const collapsed =
			next && next.policies && next.policies.length > 0 ? next : null;
		// Reset the canonical-text trackers to the form we're about to emit
		// so the parent's echo of the same value doesn't trigger a reset of
		// in-progress buffers in OTHER tabs. (The active-tab buffer is the
		// authoritative source, but its sibling tabs need to be reseeded.)
		const nextJson = serializeJson(collapsed);
		const nextYaml = serializeYaml(collapsed);
		setLastSyncedJson(nextJson);
		setLastSyncedYaml(nextYaml);
		// Refresh sibling buffers so a tab switch shows the latest value.
		// Keystroke-driven commits skip the active tab's buffer so the user's
		// in-progress text isn't clobbered by their own commit. AST-driven
		// mutations (template insert) pass `resyncBuffers: true` to
		// force-refresh the active buffer too, since the change didn't
		// originate from the active editor's text.
		if (opts.resyncBuffers || activeTab !== "json") {
			setJsonText(nextJson);
			setJsonParseError(null);
		}
		if (opts.resyncBuffers || activeTab !== "yaml") {
			setYamlText(nextYaml);
			setYamlParseError(null);
		}
		onChange(collapsed);
	}

	function commitPolicies(
		nextPolicies: Policy[],
		opts: { resyncBuffers?: boolean } = {},
	) {
		// emit() owns the empty-list -> null collapse; just hand it the shape.
		emit({ policies: nextPolicies }, opts);
	}

	function handleTemplate(key: string) {
		if (!key) return;
		const tpl = instantiateTemplate(key as PolicyTemplateKey);
		const current: Policy[] = value?.policies ?? [];
		commitPolicies([...current, tpl], { resyncBuffers: true });
		// Reset the trigger so the same template can be re-inserted next time.
		setTemplateKey("");
	}

	/**
	 * JSON-tab keystroke handler. Always updates the buffer; tries to
	 * parse and emit on every keystroke so a valid edit immediately
	 * propagates to `onChange` (and reseeds the YAML sibling). An empty
	 * buffer collapses to `null`. Invalid JSON sets the parse-error
	 * indicator and does NOT call `onChange` — the user keeps editing
	 * with the broken text intact.
	 */
	function handleJsonText(next: string) {
		setJsonText(next);
		const trimmed = next.trim();
		if (!trimmed) {
			setJsonParseError(null);
			emit(null);
			return;
		}
		try {
			const parsed = asTablePolicies(JSON.parse(next));
			setJsonParseError(null);
			emit(parsed);
		} catch (err) {
			setJsonParseError(
				err instanceof Error ? err.message : "Invalid JSON",
			);
			// Buffer is now invalid. Wipe any prior validation errors so the
			// stale-AST result doesn't keep rendering next to the new syntax
			// error. The next successful parse will re-trigger validation.
			setValidationErrors(null);
		}
	}

	/**
	 * YAML-tab keystroke handler — same contract as `handleJsonText`,
	 * but parsed via `js-yaml` with the JSON_SCHEMA (no anchors / aliases
	 * / custom types) so we round-trip cleanly into the JSON tab.
	 */
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
			const parsed = raw === null ? null : asTablePolicies(raw);
			setYamlParseError(null);
			emit(parsed);
		} catch (err) {
			setYamlParseError(
				err instanceof Error ? err.message : "Invalid YAML",
			);
			// Same reasoning as `handleJsonText`: stale validation results
			// shouldn't bleed past a parse error.
			setValidationErrors(null);
		}
	}

	/**
	 * Switch tabs, parsing/reserializing the source tab's contents into the
	 * destination grammar where needed. Returns true on success; false (and
	 * leaves `activeTab` untouched) if the source tab has an unresolved
	 * parse error so the user can fix it before moving.
	 */
	function handleTabChange(nextRaw: string) {
		const next = nextRaw as TabKey;
		if (next === activeTab) return;

		// Leaving a code tab: parse its buffer first so we have a fresh AST
		// to feed the destination tab. If parsing fails, stay put.
		if (activeTab === "json") {
			const trimmed = jsonText.trim();
			let parsed: TablePolicies | null;
			try {
				parsed = trimmed
					? asTablePolicies(JSON.parse(jsonText))
					: null;
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
			let parsed: TablePolicies | null;
			try {
				const raw = trimmed
					? yaml.load(yamlText, { schema: yaml.JSON_SCHEMA })
					: null;
				parsed = raw === null ? null : asTablePolicies(raw);
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
	// While a code tab has an unresolved parse error, AST-driven mutations
	// would silently clobber the user's broken buffer (we resync buffers on
	// commit). Disable the toolbar mutations until they fix or abandon the
	// buffer by switching tabs.
	const mutationsDisabled = activeParseError !== null;
	const mutationsDisabledTitle = mutationsDisabled
		? "Resolve the parse error in the JSON/YAML tab to use this action"
		: undefined;

	return (
		<div className="space-y-3">
			<div className="flex justify-between items-center">
				<h3 className="text-sm font-medium">Policies</h3>
				<div className="flex gap-2">
					<Select
						value={templateKey}
						onValueChange={handleTemplate}
						disabled={mutationsDisabled}
					>
						<SelectTrigger
							className="w-[200px]"
							aria-label="Insert template"
							disabled={mutationsDisabled}
							title={mutationsDisabledTitle}
						>
							<SelectValue placeholder="Insert template..." />
						</SelectTrigger>
						<SelectContent>
							{Object.keys(POLICY_TEMPLATES).map((k) => (
								<SelectItem key={k} value={k}>
									{k}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={() => setShowRef(true)}
					>
						Reference
					</Button>
				</div>
			</div>

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
						path="policies.json"
						data-testid="policy-editor-json"
					/>
				</TabsContent>

				<TabsContent value="yaml" className="min-h-[320px]">
					<CodeEditor
						mode="yaml"
						text={yamlText}
						onChange={handleYamlText}
						path="policies.yaml"
						data-testid="policy-editor-yaml"
					/>
				</TabsContent>
			</Tabs>

			{activeParseError && (
				<p
					className="text-xs text-destructive"
					role="alert"
					data-testid="policy-editor-parse-error"
				>
					Parse error: {activeParseError}
				</p>
			)}

			{!activeParseError &&
				validationErrors !== null &&
				validationErrors.length > 0 && (
					<div
						className="text-xs text-destructive space-y-0.5"
						role="alert"
						data-testid="policy-editor-validation-errors"
					>
						<p className="font-medium">Validation errors:</p>
						{validationErrors.map((err, i) => (
							<p
								// path+message is unique enough for the
								// editor's surface area; collisions would
								// only happen on duplicate identical errors.
								key={`${err.path}:${err.message}:${i}`}
								data-testid="policy-editor-validation-error"
							>
								{err.path}: {err.message}
							</p>
						))}
					</div>
				)}

			<PolicyReferencePanel
				open={showRef}
				onClose={() => setShowRef(false)}
			/>
		</div>
	);
}
