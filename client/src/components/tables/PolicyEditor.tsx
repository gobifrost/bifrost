/**
 * Top-level policy editor — a tabbed shell that exposes the same
 * `TablePolicies | null` AST through JSON and YAML views via the shared
 * `<JsonYamlEditor>` component. This file is the policy-specific shell:
 * it owns the template-insert dropdown, the reference-panel side sheet,
 * and the debounced server-side validation that surfaces shape errors
 * under the editor.
 *
 * When `value === null`, the JSON/YAML buffers seed to `{policies: []}`
 * so the user has a wrapper to paste into without manual editing. The
 * empty-collapse path (user clears the buffer → `onChange(null)`) is
 * handled inside `JsonYamlEditor`; the strict-shape validation
 * (`{policies: [...]}` as document root) is applied here via
 * `validateParsed`. The `emit` collapse of `{policies: []}` back to
 * `null` is handled in this file's `emit()`.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";

import { JsonYamlEditor } from "@/components/shared/JsonYamlEditor";
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

export interface PolicyEditorProps {
	value: TablePolicies | null;
	onChange: (next: TablePolicies | null) => void;
}

/** Empty-wrapper seed so the user has a scaffold to paste into when the
 *  AST starts out null. Mirrors the pre-extraction behavior. */
const POLICY_SEED: TablePolicies = { policies: [] };

/** Strict-shape validation: only `{policies: [...]}` is accepted as the
 *  document root. No single-Policy fallback. Throws on shape mismatch
 *  so JsonYamlEditor surfaces it as a parse error. */
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
	const [templateKey, setTemplateKey] = useState<string>("");
	const [activeParseError, setActiveParseError] = useState<string | null>(
		null,
	);

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

	// Render-phase reset: when the AST clears (parent passes value=null,
	// keystroke handler emits(null), etc.) wipe any prior validation
	// results synchronously so the UI doesn't keep showing stale errors
	// for a buffer that no longer exists.
	if (value === null && validationErrors !== null) {
		setValidationErrors(null);
	}

	// Debounced server validation. The effect runs on every `value` change
	// (which only changes when a buffer parses successfully). On rapid
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

	function emit(next: TablePolicies | null) {
		// Empty policy list collapses back to null so we don't persist
		// `{policies: []}` and accidentally lock the table down for everyone.
		const collapsed =
			next && next.policies && next.policies.length > 0 ? next : null;
		onChange(collapsed);
	}

	function handleTemplate(key: string) {
		if (!key) return;
		const tpl = instantiateTemplate(key as PolicyTemplateKey);
		const current: Policy[] = value?.policies ?? [];
		emit({ policies: [...current, tpl] });
		// Reset the trigger so the same template can be re-inserted next time.
		setTemplateKey("");
	}

	function handleParseErrorChange(error: string | null) {
		setActiveParseError(error);
		// Buffer is now invalid. Wipe any prior validation errors so the
		// stale-AST result doesn't keep rendering next to the new syntax
		// error. The next successful parse will re-trigger validation.
		if (error !== null) {
			setValidationErrors(null);
		}
	}

	const paths = useMemo(
		() => ({ json: "policies.json", yaml: "policies.yaml" }),
		[],
	);

	// While a code tab has an unresolved parse error, AST-driven mutations
	// would silently clobber the user's broken buffer. Disable the toolbar
	// mutations until they fix or abandon the buffer by switching tabs.
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
					<PolicyReferencePanel />
				</div>
			</div>

			<JsonYamlEditor<TablePolicies>
				value={value}
				onChange={emit}
				schema={{}}
				seed={POLICY_SEED}
				paths={paths}
				validateParsed={asTablePolicies}
				onParseErrorChange={handleParseErrorChange}
				hideParseError
			/>

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

		</div>
	);
}
