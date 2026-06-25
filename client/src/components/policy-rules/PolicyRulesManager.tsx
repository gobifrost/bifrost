/**
 * PolicyRulesManager — inline CRUD manager for named policy rules.
 *
 * Lists all rules for a given domain; lets admins create, edit, and delete
 * them. Built-in rules (is_builtin=true) render read-only with a badge.
 *
 * DELETE path surfaces the 409+usages blast-radius before the rule is gone.
 * SAVE path pre-fetches usages when editing so the user sees impact before
 * committing a change.
 */

import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, Loader2, Lock } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogFooter,
} from "@/components/ui/dialog";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";

import {
	listPolicyRules,
	createPolicyRule,
	updatePolicyRule,
	deletePolicyRule,
	policyRuleUsages,
	type PolicyRule,
	type PolicyRuleInUseError,
} from "@/services/policyRules";

export interface PolicyRulesManagerProps {
	domain: "file" | "table";
}

interface RuleFormState {
	name: string;
	description: string;
	/** Serialised JSON body */
	bodyJson: string;
}

const EMPTY_FORM: RuleFormState = { name: "", description: "", bodyJson: '{\n  "actions": ["read"],\n  "when": null\n}' };

function isInUseError(err: unknown): err is Error & { cause: PolicyRuleInUseError } {
	return (
		err instanceof Error &&
		typeof (err as Error & { cause?: unknown }).cause === "object" &&
		(err as Error & { cause?: unknown }).cause !== null &&
		(
			(err as Error & { cause?: { type?: unknown } }).cause as { type?: unknown }
		).type === "in_use"
	);
}

export function PolicyRulesManager({ domain }: PolicyRulesManagerProps) {
	const [rules, setRules] = useState<PolicyRule[]>([]);
	const [loading, setLoading] = useState(true);

	// Form dialog: null = closed, { mode:"create" } = creating, { mode:"edit", rule } = editing
	const [formTarget, setFormTarget] = useState<
		| { mode: "create" }
		| { mode: "edit"; rule: PolicyRule }
		| null
	>(null);
	const [form, setForm] = useState<RuleFormState>(EMPTY_FORM);
	const [formError, setFormError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);

	// Delete confirmation
	const [deleteTarget, setDeleteTarget] = useState<PolicyRule | null>(null);
	const [deleting, setDeleting] = useState(false);

	// Blast-radius state: populated when DELETE returns 409
	const [blastRadius, setBlastRadius] = useState<PolicyRuleInUseError | null>(null);

	// Edit usages: informational — shows impact before saving, does NOT block editing
	const [editUsages, setEditUsages] = useState<{ file_policies: PolicyRuleInUseError["usages"]["file_policies"]; tables: PolicyRuleInUseError["usages"]["tables"]; total: number } | null>(null);

	async function reload() {
		try {
			const data = await listPolicyRules(domain);
			setRules(data);
		} catch {
			toast.error("Failed to load policy rules");
		} finally {
			setLoading(false);
		}
	}

	useEffect(() => {
		// Call reload in a nested async fn so setState calls remain inside a
		// callback and satisfy react-hooks/set-state-in-effect.
		const run = async () => { await reload(); };
		void run();
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [domain]);

	function openCreate() {
		setForm(EMPTY_FORM);
		setFormError(null);
		setFormTarget({ mode: "create" });
	}

	function openEdit(rule: PolicyRule) {
		setForm({
			name: rule.name,
			description: rule.description ?? "",
			bodyJson: JSON.stringify(rule.body, null, 2),
		});
		setFormError(null);
		setEditUsages(null);
		setFormTarget({ mode: "edit", rule });
	}

	function closeForm() {
		setFormTarget(null);
		setFormError(null);
		setEditUsages(null);
	}

	async function handleSave() {
		setFormError(null);
		let parsed: Record<string, unknown>;
		try {
			parsed = JSON.parse(form.bodyJson) as Record<string, unknown>;
		} catch {
			setFormError("Body must be valid JSON.");
			return;
		}

		setSaving(true);
		try {
			if (formTarget?.mode === "create") {
				await createPolicyRule({
					name: form.name.trim(),
					domain,
					description: form.description.trim() || null,
					body: parsed,
				});
				toast.success("Policy rule created");
			} else if (formTarget?.mode === "edit") {
				const rule = formTarget.rule;
				await updatePolicyRule(rule.domain, rule.name, {
					name: form.name.trim() !== rule.name ? form.name.trim() : undefined,
					description: form.description.trim() || null,
					body: parsed,
				});
				toast.success("Policy rule updated");
			}
			closeForm();
			void reload();
		} catch (err) {
			setFormError(err instanceof Error ? err.message : "Save failed");
		} finally {
			setSaving(false);
		}
	}

	function openDelete(rule: PolicyRule) {
		setDeleteTarget(rule);
		setBlastRadius(null);
	}

	async function handleDelete() {
		if (!deleteTarget) return;
		setDeleting(true);
		try {
			await deletePolicyRule(deleteTarget.domain, deleteTarget.name);
			toast.success("Policy rule deleted");
			setDeleteTarget(null);
			void reload();
		} catch (err) {
			if (isInUseError(err)) {
				setBlastRadius(err.cause);
			} else {
				toast.error("Failed to delete policy rule", {
					description: err instanceof Error ? err.message : String(err),
				});
				setDeleteTarget(null);
			}
		} finally {
			setDeleting(false);
		}
	}

	async function handlePreviewUsages(rule: PolicyRule) {
		try {
			const usages = await policyRuleUsages(rule.domain, rule.name);
			if (usages.total > 0) {
				// Informational only — editing a referenced rule IS allowed.
				// Store usages separately so the edit dialog can show an impact banner
				// without triggering the delete/blast-radius AlertDialog.
				setEditUsages(usages);
			}
		} catch {
			// Best-effort — if usages fetch fails, just proceed with the edit.
		}
	}

	const domainLabel = domain === "file" ? "file" : "table";

	return (
		<div className="space-y-3" data-testid="policy-rules-manager">
			<div className="flex items-center justify-between">
				<p className="text-sm text-muted-foreground">
					Reusable named rules for {domainLabel} policies
				</p>
				<Button
					type="button"
					size="sm"
					variant="outline"
					onClick={openCreate}
					data-testid="policy-rules-create-btn"
				>
					<Plus className="h-4 w-4 mr-1" />
					New rule
				</Button>
			</div>

			{loading ? (
				<div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
					<Loader2 className="h-4 w-4 animate-spin" />
					Loading rules…
				</div>
			) : rules.length === 0 ? (
				<p className="text-sm text-muted-foreground py-4 text-center">
					No {domainLabel} policy rules yet.
				</p>
			) : (
				<Table>
					<TableHeader>
						<TableRow>
							<TableHead>Name</TableHead>
							<TableHead>Description</TableHead>
							<TableHead className="w-32" />
						</TableRow>
					</TableHeader>
					<TableBody>
						{rules.map((rule) => (
							<TableRow key={rule.id} data-testid="policy-rule-row">
								<TableCell className="font-mono text-sm">
									<div className="flex items-center gap-2">
										{rule.name}
										{rule.is_builtin && (
											<Badge
												variant="secondary"
												className="gap-1 text-xs"
												data-testid="builtin-badge"
											>
												<Lock className="h-3 w-3" />
												built-in
											</Badge>
										)}
									</div>
								</TableCell>
								<TableCell className="text-sm text-muted-foreground">
									{rule.description ?? "—"}
								</TableCell>
								<TableCell>
									{!rule.is_builtin && (
										<div className="flex items-center justify-end gap-1">
											<Button
												size="icon"
												variant="ghost"
												aria-label={`Edit ${rule.name}`}
												onClick={() => {
													openEdit(rule);
													void handlePreviewUsages(rule);
												}}
												data-testid="policy-rule-edit-btn"
											>
												<Pencil className="h-4 w-4" />
											</Button>
											<Button
												size="icon"
												variant="ghost"
												className="text-destructive hover:text-destructive"
												aria-label={`Delete ${rule.name}`}
												onClick={() => openDelete(rule)}
												data-testid="policy-rule-delete-btn"
											>
												<Trash2 className="h-4 w-4" />
											</Button>
										</div>
									)}
								</TableCell>
							</TableRow>
						))}
					</TableBody>
				</Table>
			)}

			{/* Create / Edit dialog */}
			<Dialog open={formTarget !== null} onOpenChange={(open) => { if (!open) closeForm(); }}>
				<DialogContent className="sm:max-w-lg">
					<DialogHeader>
						<DialogTitle>
							{formTarget?.mode === "create" ? "Create policy rule" : `Edit "${formTarget?.mode === "edit" ? formTarget.rule.name : ""}"`}
						</DialogTitle>
					</DialogHeader>

					<div className="space-y-4 py-2">
						<div className="space-y-1.5">
							<Label htmlFor="rule-name">Name</Label>
							<Input
								id="rule-name"
								value={form.name}
								onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
								placeholder="e.g. admin_access"
								disabled={formTarget?.mode !== "create"}
							/>
							{formTarget?.mode !== "create" && (
								<p className="text-xs text-muted-foreground">
									Rule names cannot be changed after creation.
								</p>
							)}
						</div>

						<div className="space-y-1.5">
							<Label htmlFor="rule-description">Description</Label>
							<Input
								id="rule-description"
								value={form.description}
								onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
								placeholder="Short description (optional)"
							/>
						</div>

						<div className="space-y-1.5">
							<Label htmlFor="rule-body">Body (JSON)</Label>
							<Textarea
								id="rule-body"
								value={form.bodyJson}
								onChange={(e) => setForm((f) => ({ ...f, bodyJson: e.target.value }))}
								className="font-mono text-xs min-h-[160px]"
								data-testid="rule-body-textarea"
							/>
						</div>

						{formTarget?.mode === "edit" && editUsages && editUsages.total > 0 && (
							<div
								className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200"
								role="status"
								data-testid="edit-usages-banner"
							>
								This rule is referenced by{" "}
								{editUsages.file_policies.length > 0 && (
									<span>{editUsages.file_policies.length} file {editUsages.file_policies.length === 1 ? "policy" : "policies"}</span>
								)}
								{editUsages.file_policies.length > 0 && editUsages.tables.length > 0 && " and "}
								{editUsages.tables.length > 0 && (
									<span>{editUsages.tables.length} {editUsages.tables.length === 1 ? "table" : "tables"}</span>
								)}
								. Saving changes will apply everywhere it&apos;s used.
							</div>
						)}

						{formError && (
							<p className="text-sm text-destructive" role="alert" data-testid="form-error">
								{formError}
							</p>
						)}
					</div>

					<DialogFooter>
						<Button variant="outline" onClick={closeForm} disabled={saving}>
							Cancel
						</Button>
						<Button onClick={handleSave} disabled={saving || !form.name.trim()}>
							{saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
							{formTarget?.mode === "create" ? "Create" : "Save"}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Delete confirmation — plain when rule has no usages */}
			<AlertDialog
				open={deleteTarget !== null && blastRadius === null}
				onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete policy rule?</AlertDialogTitle>
						<AlertDialogDescription>
							<span>
								Delete <span className="font-mono">{deleteTarget?.name}</span>?
								This cannot be undone.
							</span>
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDelete}
							disabled={deleting}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Blast-radius dialog: shown when DELETE returns 409 (delete-flow only) */}
			<AlertDialog
				open={blastRadius !== null}
				onOpenChange={(open) => { if (!open) { setBlastRadius(null); setDeleteTarget(null); } }}
			>
				<AlertDialogContent data-testid="blast-radius-dialog">
					<AlertDialogHeader>
						<AlertDialogTitle>Rule is in use</AlertDialogTitle>
						<AlertDialogDescription asChild>
							<div className="space-y-3">
								<p>{blastRadius?.message}</p>
								{blastRadius && blastRadius.usages.file_policies.length > 0 && (
									<div>
										<p className="font-medium text-sm mb-1">
											File policies ({blastRadius.usages.file_policies.length})
										</p>
										<ul className="text-xs space-y-0.5 list-disc list-inside">
											{blastRadius.usages.file_policies.map((fp) => (
												<li key={fp.id} data-testid="blast-file-policy">
													{fp.location}/{fp.path}
												</li>
											))}
										</ul>
									</div>
								)}
								{blastRadius && blastRadius.usages.tables.length > 0 && (
									<div>
										<p className="font-medium text-sm mb-1">
											Tables ({blastRadius.usages.tables.length})
										</p>
										<ul className="text-xs space-y-0.5 list-disc list-inside">
											{blastRadius.usages.tables.map((tb) => (
												<li key={tb.id} data-testid="blast-table">
													{tb.name}
												</li>
											))}
										</ul>
									</div>
								)}
								<p className="text-sm text-destructive font-medium">
									Deleting this rule will affect all of the above.
									You must remove all references first.
								</p>
							</div>
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Close</AlertDialogCancel>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
