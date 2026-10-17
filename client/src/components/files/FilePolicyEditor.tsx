import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListChecks, Save, Trash2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { JsonYamlEditor } from "@/components/shared/JsonYamlEditor";
import type { FilePolicy, FilePolicies } from "@/services/filePolicies";
import {
	FILE_POLICY_TEMPLATES,
	instantiateFileTemplate,
	type FilePolicyTemplateKey,
} from "./file-policy-templates";
import { FilePolicyReferencePanel } from "./FilePolicyReferencePanel";
import { listPolicyRules, type PolicyRule } from "@/services/policyRules";

interface FilePolicyEditorProps {
	path: string;
	value: FilePolicy;
	onSave: (policy: FilePolicy) => void | Promise<void>;
	onDelete: (policy: FilePolicy) => void | Promise<void>;
	onBusyChange?: (busy: boolean) => void;
	compact?: boolean;
	rulesRefreshKey?: string | number;
}

const POLICY_SEED: FilePolicies = { policies: [] };
const ACTION_ORDER = ["read", "write", "delete", "list"] as const;

/** Only `{policies: [...]}` is accepted as the document root. */
function asFilePolicies(parsed: unknown): FilePolicies {
	if (
		parsed === null ||
		typeof parsed !== "object" ||
		Array.isArray(parsed)
	) {
		throw new Error(
			"Document root must be an object with a `policies` key.",
		);
	}
	if (!Array.isArray((parsed as Record<string, unknown>).policies)) {
		throw new Error("`policies` must be an array.");
	}
	return parsed as FilePolicies;
}

interface SaveError {
	path: string;
	message: string;
}

/** Parse the structured 422 detail from a save attempt. */
function extractSaveErrors(err: unknown): SaveError[] | null {
	if (!(err instanceof Error)) return null;
	// parseResponse() in filePolicies.ts serializes the detail object via
	// JSON.stringify when it isn't a plain string. Try to parse it back.
	try {
		const parsed = JSON.parse(err.message) as { errors?: SaveError[] };
		if (Array.isArray(parsed?.errors)) return parsed.errors;
	} catch {
		// Not JSON — fall through to return null.
	}
	return null;
}

export function FilePolicyEditor({
	path,
	value,
	onSave,
	onDelete,
	onBusyChange,
	compact = false,
	rulesRefreshKey,
}: FilePolicyEditorProps) {
	// The editor mutates only the inner policy document; the location/path/org
	// wrapper is fixed by the selection and reattached on save.
	const mutationBusy = useRef(false);
	const errorRef = useRef<HTMLDivElement>(null);
	const [doc, setDoc] = useState<FilePolicies | null>(value.policies ?? null);
	const [parseError, setParseError] = useState<string | null>(null);
	const [templateKey, setTemplateKey] = useState<string>("");
	const [refKey, setRefKey] = useState<string>("");
	const [saving, setSaving] = useState(false);
	const [deleting, setDeleting] = useState(false);
	const [saveErrors, setSaveErrors] = useState<SaveError[] | null>(null);
	const [mutationError, setMutationError] = useState<string | null>(null);
	const [retryAction, setRetryAction] = useState<"save" | "delete" | null>(
		null,
	);
	const [rules, setRules] = useState<PolicyRule[]>([]);
	const [rulesError, setRulesError] = useState<string | null>(null);
	const [compactMode, setCompactMode] = useState<"rules" | "code">("rules");

	useEffect(() => {
		if (saveErrors || mutationError) {
			errorRef.current?.focus();
			errorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [saveErrors, mutationError]);

	const loadRules = useCallback(() => {
		return listPolicyRules("file")
			.then((next) => {
				setRulesError(null);
				setRules(next);
			})
			.catch(() => {
				setRules([]);
				setRulesError("Unable to load file policy rules.");
			});
	}, []);

	useEffect(() => {
		void loadRules();
	}, [loadRules, rulesRefreshKey]);

	useEffect(() => {
		onBusyChange?.(saving || deleting);
	}, [deleting, onBusyChange, saving]);

	const paths = useMemo(
		() => ({ json: "file-policies.json", yaml: "file-policies.yaml" }),
		[],
	);

	function handleTemplate(key: string) {
		if (!key) return;
		const tpl = instantiateFileTemplate(key as FilePolicyTemplateKey);
		const current = doc?.policies ?? [];
		setDoc({ policies: [...current, tpl] });
		setTemplateKey("");
	}

	function handleRef(name: string) {
		if (!name) return;
		const current = doc?.policies ?? [];
		setDoc({ policies: [...current, { $ref: name }] });
		setRefKey("");
	}

	function handleRemoveRule(index: number) {
		const current = doc?.policies ?? [];
		setDoc({
			...(doc ?? POLICY_SEED),
			policies: current.filter((_, i) => i !== index),
		});
		setSaveErrors(null);
		setMutationError(null);
		setRetryAction(null);
	}

	async function handleSave() {
		if (mutationBusy.current || mutationsDisabled) return;
		mutationBusy.current = true;
		setRetryAction(null);
		setSaving(true);
		setSaveErrors(null);
		setMutationError(null);
		try {
			await onSave({ ...value, policies: doc ?? { policies: [] } });
		} catch (err) {
			const structured = extractSaveErrors(err);
			if (structured) {
				setSaveErrors(structured);
				setRetryAction("save");
			} else {
				setMutationError(
					err instanceof Error ? err.message : String(err),
				);
				setRetryAction("save");
			}
		} finally {
			mutationBusy.current = false;
			setSaving(false);
		}
	}

	async function handleDelete() {
		if (mutationBusy.current || mutationsDisabled || !value.id) return;
		mutationBusy.current = true;
		setRetryAction(null);
		setDeleting(true);
		setSaveErrors(null);
		setMutationError(null);
		try {
			await onDelete(value);
		} catch (err) {
			setMutationError(err instanceof Error ? err.message : String(err));
			setRetryAction("delete");
		} finally {
			mutationBusy.current = false;
			setDeleting(false);
		}
	}

	function handleRetry() {
		if (mutationsDisabled) return;
		if (retryAction === "delete") {
			void handleDelete();
			return;
		}
		void handleSave();
	}

	const mutationsDisabled = parseError !== null || saving || deleting;
	const busy = saving || deleting;
	const effectivePath = path || value.path;
	const templateOptions = useMemo<ComboboxOption[]>(
		() =>
			Object.entries(FILE_POLICY_TEMPLATES).map(([key, template]) => ({
				value: key,
				label: readableRuleName(key),
				description: template.description ?? undefined,
			})),
		[],
	);
	const ruleOptions = useMemo<ComboboxOption[]>(
		() =>
			rules.map((rule) => ({
				value: rule.name,
				label: readableRuleName(rule.name),
				description:
					rule.description ??
					"Shared rule from the Shared Rule Library",
			})),
		[rules],
	);
	const editorClassName = compact
		? "flex min-h-0 flex-1 flex-col gap-3"
		: "flex min-h-0 flex-1 flex-col gap-3";
	const scrollClassName = compact
		? "space-y-3"
		: "min-h-0 flex-1 space-y-3 overflow-y-auto pr-1";

	return (
		<section className={editorClassName}>
			<div className={scrollClassName}>
				{!compact && (
					<div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
						<div className="space-y-1">
							<p className="break-words text-xs font-medium text-foreground">
								{value.location}
							</p>
							<p className="break-words text-xs text-muted-foreground">
								{effectivePath
									? `/${effectivePath}`
									: "/(root)"}
							</p>
						</div>
					</div>
				)}

				{compact && (
					<div className="flex min-h-11 items-center justify-between gap-3">
						<div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
							<ListChecks className="size-4 text-primary" />
							Access Rules
						</div>
						<label className="flex min-h-11 cursor-pointer items-center gap-2 text-xs text-muted-foreground">
							Advanced
							<Switch
								aria-label="Advanced"
								checked={compactMode === "code"}
								disabled={mutationsDisabled}
								onCheckedChange={(checked) =>
									setCompactMode(checked ? "code" : "rules")
								}
							/>
						</label>
					</div>
				)}
				<div
					className={
						compact
							? "grid grid-cols-2 gap-2"
							: "flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center"
					}
				>
					<Combobox
						value={templateKey}
						onValueChange={handleTemplate}
						options={templateOptions}
						placeholder="Add Template..."
						searchPlaceholder="Search templates..."
						emptyText="No templates found."
						disabled={mutationsDisabled}
						className={
							compact
								? "h-11 min-h-11 w-full min-w-0 px-2 text-xs sm:min-h-11"
								: "h-11 min-h-11 w-full min-w-0 sm:w-[200px] sm:min-h-11"
						}
						aria-label="Add Template"
					/>
					{(compact || rules.length > 0) && (
						<div
							className="min-w-0"
							onFocusCapture={() => {
								void loadRules();
							}}
							onPointerDownCapture={() => {
								void loadRules();
							}}
						>
							<Combobox
								value={refKey}
								onValueChange={handleRef}
								options={ruleOptions}
								placeholder="Add Shared Rule..."
								searchPlaceholder="Search shared rules..."
								emptyText="No matching shared rules. Create one in the Shared Rule Library."
								disabled={mutationsDisabled}
								className={
									compact
										? "h-11 min-h-11 w-full min-w-0 px-2 text-xs sm:min-h-11"
										: "h-11 min-h-11 w-full min-w-0 sm:w-[200px] sm:min-h-11"
								}
								aria-label="Add Shared Rule"
							/>
						</div>
					)}
					{!compact && (
						<div className="shrink-0 sm:ml-auto">
							<FilePolicyReferencePanel />
						</div>
					)}
				</div>

				{!compact || compactMode === "code" ? (
					<div className={compact ? "space-y-2" : undefined}>
						{compact && (
							<p className="text-xs text-muted-foreground">
								Edit rule conditions in YAML or JSON.
							</p>
						)}
						{compact && (
							<div className="flex justify-end">
								<FilePolicyReferencePanel />
							</div>
						)}
						<JsonYamlEditor<FilePolicies>
							value={doc}
							onChange={(next) => {
								setDoc(next);
								setSaveErrors(null);
								setMutationError(null);
								setRetryAction(null);
							}}
							schema={{}}
							seed={POLICY_SEED}
							defaultFormat="yaml"
							paths={paths}
							validateParsed={asFilePolicies}
							onParseErrorChange={setParseError}
							hideParseError
						/>
					</div>
				) : (
					<ReadablePolicyRules
						doc={doc}
						sharedRules={rules}
						onRemove={handleRemoveRule}
						disabled={mutationsDisabled}
					/>
				)}

				{rulesError && (
					<div className="rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/30 p-3 text-xs text-muted-foreground">
						<p className="font-medium text-foreground">
							{rulesError}
						</p>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="mt-2 h-11 px-3"
							onClick={() => {
								void loadRules();
							}}
						>
							Retry Loading Rules
						</Button>
					</div>
				)}
			</div>

			{parseError && (
				<p className="shrink-0 text-xs text-destructive" role="alert">
					Parse error: {parseError}
				</p>
			)}

			{!parseError && (saveErrors || mutationError) && (
				<div
					ref={errorRef}
					tabIndex={-1}
					className="outline-none max-h-32 shrink-0 space-y-1 overflow-y-auto rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/20 p-3 text-xs text-destructive [overflow-wrap:anywhere]"
					role="alert"
					data-testid="file-policy-save-errors"
				>
					{saveErrors && saveErrors.length > 0 && (
						<>
							<p className="font-medium">Save errors:</p>
							{saveErrors.map((err, i) => (
								<p
									key={`${err.path}:${err.message}:${i}`}
									data-testid="file-policy-save-error"
								>
									{err.path}: {err.message}
								</p>
							))}
						</>
					)}
					{mutationError && (
						<>
							<p className="font-medium">
								{retryAction === "delete"
									? "Delete failed:"
									: "Save failed:"}
							</p>
							<p>{mutationError}</p>
						</>
					)}
					<Button
						type="button"
						variant="ghost"
						size="sm"
						className="h-11 px-3"
						onClick={handleRetry}
					>
						Retry {retryAction === "delete" ? "Delete" : "Save"}
					</Button>
				</div>
			)}

			<div className="flex items-center justify-end gap-2 border-t border-border/60 pt-3">
				<Button
					type="button"
					variant="outline"
					size="sm"
					className="h-11"
					onClick={handleDelete}
					disabled={!value.id || busy || parseError !== null}
				>
					<Trash2 className="h-4 w-4" />
					{deleting ? "Deleting..." : "Delete"}
				</Button>
				<Button
					type="button"
					size="sm"
					className="h-11"
					onClick={handleSave}
					disabled={mutationsDisabled}
				>
					<Save className="h-4 w-4" />
					{saving ? "Saving..." : "Save Policy"}
				</Button>
			</div>
		</section>
	);
}

function isPolicyRef(rule: unknown): rule is { $ref: string } {
	return (
		rule !== null &&
		typeof rule === "object" &&
		typeof (rule as { $ref?: unknown }).$ref === "string"
	);
}

function isDirectRule(rule: unknown): rule is {
	name?: string;
	description?: string | null;
	actions?: unknown;
	when?: unknown;
} {
	return rule !== null && typeof rule === "object" && !isPolicyRef(rule);
}

function actionsFromUnknown(value: unknown): string[] {
	if (!Array.isArray(value)) return [];
	const actions = value.filter((action): action is string => {
		return (
			typeof action === "string" &&
			(ACTION_ORDER as readonly string[]).includes(action)
		);
	});
	return ACTION_ORDER.filter((action) => actions.includes(action));
}

function summarizePredicate(when: unknown) {
	if (when === null || when === undefined) return "Applies to everyone.";
	if (
		typeof when === "object" &&
		"authority" in when &&
		(when as { authority?: unknown }).authority === "platform_admin"
	) {
		return "Requires Platform Admin Access.";
	}
	if (
		typeof when === "object" &&
		"user" in when &&
		(when as { user?: unknown }).user === "is_platform_admin"
	) {
		return "Requires the Platform Admin role.";
	}
	if (
		typeof when === "object" &&
		"call" in when &&
		(when as { call?: unknown }).call === "has_role"
	) {
		const args = (when as { args?: unknown }).args;
		const role =
			Array.isArray(args) && typeof args[0] === "string" ? args[0] : "";
		return role ? `Requires role ${role}.` : "Requires a role.";
	}
	if (
		typeof when === "object" &&
		"eq" in when &&
		JSON.stringify((when as { eq?: unknown }).eq) ===
			JSON.stringify([{ file: "created_by" }, { user: "user_id" }])
	) {
		return "Applies to files uploaded by the user.";
	}
	return "Uses a custom predicate.";
}

function ReadablePolicyRules({
	doc,
	sharedRules,
	onRemove,
	disabled,
}: {
	doc: FilePolicies | null;
	sharedRules: PolicyRule[];
	onRemove: (index: number) => void;
	disabled: boolean;
}) {
	const policies = doc?.policies ?? [];
	const sharedByName = new Map(sharedRules.map((rule) => [rule.name, rule]));
	if (policies.length === 0) {
		return (
			<div className="rounded-[var(--bf-radius-surface)] border border-dashed border-border/80 p-4 text-sm text-muted-foreground">
				No policy rules yet. Add a template or shared rule, then save
				the policy.
			</div>
		);
	}
	return (
		<ul aria-label="Policy rules" className="space-y-2">
			{policies.map((rule, index) => {
				const ref = isPolicyRef(rule)
					? sharedByName.get(rule.$ref)
					: null;
				const body = ref?.body;
				const actions = isDirectRule(rule)
					? actionsFromUnknown(rule.actions)
					: actionsFromUnknown(
							body && typeof body === "object"
								? (body as { actions?: unknown }).actions
								: undefined,
						);
				const name = isPolicyRef(rule)
					? rule.$ref
					: typeof rule.name === "string" && rule.name
						? rule.name
						: `Rule ${index + 1}`;
				const displayName = readableRuleName(name);
				const description = isPolicyRef(rule)
					? (ref?.description ?? "Shared rule")
					: isDirectRule(rule)
						? rule.description
						: null;
				const when = isPolicyRef(rule)
					? body && typeof body === "object"
						? (body as { when?: unknown }).when
						: undefined
					: isDirectRule(rule)
						? rule.when
						: undefined;
				const unresolvedRef = isPolicyRef(rule) && !ref;
				return (
					<li
						key={`${name}:${index}`}
						className="space-y-2 rounded-[var(--bf-radius-surface)] border border-border/70 p-3"
					>
						<div className="flex flex-wrap items-center gap-2">
							<p className="min-w-0 flex-1 break-words text-sm font-medium">
								{displayName}
							</p>
							{isPolicyRef(rule) && (
								<Badge
									variant="outline"
									className="border-primary/20 text-primary"
								>
									Shared
								</Badge>
							)}
							<Button
								type="button"
								variant="ghost"
								size="sm"
								className="h-8 px-2 text-xs"
								disabled={disabled}
								onClick={() => onRemove(index)}
							>
								<Trash2 className="h-4 w-4" />
								Remove
							</Button>
						</div>
						{description && (
							<p className="break-words text-xs text-muted-foreground">
								{description}
							</p>
						)}
						<div className="flex flex-wrap gap-1.5">
							{unresolvedRef ? (
								<Badge variant="outline">shared rule</Badge>
							) : actions.length > 0 ? (
								actions.map((action) => (
									<Badge
										key={action}
										variant="secondary"
										className="bg-primary/10 text-primary"
									>
										{action}
									</Badge>
								))
							) : (
								<Badge variant="outline">custom actions</Badge>
							)}
						</div>
						<p className="text-xs text-muted-foreground">
							{unresolvedRef
								? "Shared rule details are unavailable."
								: summarizePredicate(when)}
						</p>
					</li>
				);
			})}
		</ul>
	);
}

function readableRuleName(name: string) {
	if (name === "admin_bypass") return "Platform Admin Access";
	return name
		.split(/[_-]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");
}
