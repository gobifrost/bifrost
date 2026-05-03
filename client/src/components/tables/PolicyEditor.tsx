/**
 * Top-level policy editor — a tabbed shell that exposes the same
 * `TablePolicies | null` AST through three views:
 *   - Form: a graphical rule list (filled in by Task 2)
 *   - JSON: a Monaco JSON editor (filled in by Task 3)
 *   - YAML: a Monaco YAML editor (filled in by Task 3)
 *
 * The shell owns the per-tab text buffers and the parse/reserialize
 * plumbing so tabs can swap freely without losing in-progress edits or
 * silently dropping invalid input. Tabs that aren't yet implemented
 * render placeholder stubs; the buffer plumbing is already wired so
 * future tasks just slot their editors into the existing contracts.
 *
 * The parent (e.g. `TableDialog`) passes the current `TablePolicies | null`
 * and a setter, and is responsible for shipping the resulting structure
 * in the create/update request body.
 */

import { useMemo, useState } from "react";
import { Plus } from "lucide-react";
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

import { PolicyReferencePanel } from "./PolicyReferencePanel";
import {
	POLICY_TEMPLATES,
	instantiateTemplate,
	type Policy,
	type PolicyTemplateKey,
} from "./policy-templates";
import type { components } from "@/lib/v1";

type TablePolicies = components["schemas"]["TablePolicies"];

type TabKey = "form" | "json" | "yaml";

export interface PolicyEditorProps {
	value: TablePolicies | null;
	onChange: (next: TablePolicies | null) => void;
}

function serializeJson(value: TablePolicies | null): string {
	return value ? JSON.stringify(value, null, 2) : "";
}

function serializeYaml(value: TablePolicies | null): string {
	return value ? yaml.dump(value) : "";
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
	const [activeTab, setActiveTab] = useState<TabKey>("form");
	const [showRef, setShowRef] = useState(false);
	const [templateKey, setTemplateKey] = useState<string>("");

	// Per-tab text buffers. The Form tab works directly off `value` so it
	// has no buffer of its own. JSON/YAML keep their own text so a partial
	// edit isn't reverted to the canonical serialization on every keystroke.
	const [jsonText, setJsonText] = useState<string>(() => serializeJson(value));
	const [yamlText, setYamlText] = useState<string>(() => serializeYaml(value));
	const [jsonParseError, setJsonParseError] = useState<string | null>(null);
	const [yamlParseError, setYamlParseError] = useState<string | null>(null);

	// `lastSynced{Json,Yaml}` track the canonical text we either emitted or
	// last accepted from props. The render-phase reset below uses these to
	// distinguish "external value changed" from "we just echoed our own
	// commit back" (we don't want to clobber a mid-typed buffer in the
	// latter case). Same pattern as PolicyEditorRow's `lastSynced`.
	const [lastSyncedJson, setLastSyncedJson] = useState<string>(() =>
		serializeJson(value),
	);
	const [lastSyncedYaml, setLastSyncedYaml] = useState<string>(() =>
		serializeYaml(value),
	);

	const externalJson = useMemo(() => serializeJson(value), [value]);
	const externalYaml = useMemo(() => serializeYaml(value), [value]);

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
		// mutations (template insert / add policy / remove policy) pass
		// `resyncBuffers: true` to force-refresh the active buffer too, since
		// the change didn't originate from the active editor's text.
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

	function addBlank() {
		const current: Policy[] = value?.policies ?? [];
		commitPolicies(
			[
				...current,
				{
					name: "new_policy",
					description: null,
					actions: ["read"],
					when: null,
				},
			],
			{ resyncBuffers: true },
		);
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
		// Form tab leaves `value` already current — no work needed.

		setActiveTab(next);
	}

	const policies: Policy[] = value?.policies ?? [];
	const activeParseError =
		activeTab === "json"
			? jsonParseError
			: activeTab === "yaml"
				? yamlParseError
				: null;
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
					<TabsTrigger value="form">Form</TabsTrigger>
					<TabsTrigger value="json">JSON</TabsTrigger>
					<TabsTrigger value="yaml">YAML</TabsTrigger>
				</TabsList>

				<TabsContent value="form" className="min-h-[320px]">
					{policies.length === 0 ? (
						<p className="text-sm text-muted-foreground">
							No policies. Without a policy, only the table owner
							and platform admins can access rows. Use a template
							or click "Add policy" to start.
						</p>
					) : (
						<div
							data-testid="form-tab-stub"
							data-policy-count={policies.length}
							className="text-sm text-muted-foreground"
						>
							Form tab (placeholder) — {policies.length} polic
							{policies.length === 1 ? "y" : "ies"}
						</div>
					)}
					<div className="mt-3">
						<Button
							type="button"
							variant="outline"
							size="sm"
							onClick={addBlank}
							disabled={mutationsDisabled}
							title={mutationsDisabledTitle}
						>
							<Plus className="h-4 w-4 mr-1" />
							Add policy
						</Button>
					</div>
				</TabsContent>

				<TabsContent value="json" className="min-h-[320px]">
					<div
						data-testid="json-tab-stub"
						data-buffer={jsonText}
						className="text-sm text-muted-foreground"
					>
						JSON tab (placeholder) — buffer is wired; Monaco lands
						in Task 3.
					</div>
				</TabsContent>

				<TabsContent value="yaml" className="min-h-[320px]">
					<div
						data-testid="yaml-tab-stub"
						data-buffer={yamlText}
						className="text-sm text-muted-foreground"
					>
						YAML tab (placeholder) — buffer is wired; Monaco lands
						in Task 3.
					</div>
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

			<PolicyReferencePanel
				open={showRef}
				onClose={() => setShowRef(false)}
			/>
		</div>
	);
}
