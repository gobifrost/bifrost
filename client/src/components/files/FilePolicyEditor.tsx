import { useEffect, useMemo, useState } from "react";
import { Save, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
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
}

const POLICY_SEED: FilePolicies = { policies: [] };

/** Only `{policies: [...]}` is accepted as the document root. */
function asFilePolicies(parsed: unknown): FilePolicies {
	if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("Document root must be an object with a `policies` key.");
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
}: FilePolicyEditorProps) {
	// The editor mutates only the inner policy document; the location/path/org
	// wrapper is fixed by the selection and reattached on save.
	const [doc, setDoc] = useState<FilePolicies | null>(value.policies ?? null);
	const [parseError, setParseError] = useState<string | null>(null);
	const [templateKey, setTemplateKey] = useState<string>("");
	const [refKey, setRefKey] = useState<string>("");
	const [saving, setSaving] = useState(false);
	const [saveErrors, setSaveErrors] = useState<SaveError[] | null>(null);
	const [rules, setRules] = useState<PolicyRule[]>([]);

	useEffect(() => {
		listPolicyRules("file")
			.then(setRules)
			.catch(() => {
				// Best-effort — if the fetch fails the dropdown is just empty.
			});
	}, []);

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

	async function handleSave() {
		setSaving(true);
		setSaveErrors(null);
		try {
			await onSave({ ...value, policies: doc ?? { policies: [] } });
		} catch (err) {
			const structured = extractSaveErrors(err);
			if (structured) {
				setSaveErrors(structured);
			} else {
				// Re-throw so the caller's toast handler sees it.
				throw err;
			}
		} finally {
			setSaving(false);
		}
	}

	const mutationsDisabled = parseError !== null;

	return (
		<section className="flex min-h-0 flex-col gap-3">
			<div className="flex items-center justify-between gap-3">
				<p className="truncate text-xs text-muted-foreground">
					{value.location}
					{path || value.path ? ` / ${path || value.path}` : " / (root)"}
				</p>
				<Badge variant="outline">{value.location}</Badge>
			</div>

			<div className="flex items-center justify-between gap-2">
				<div className="flex items-center gap-2">
					<Select
						value={templateKey}
						onValueChange={handleTemplate}
						disabled={mutationsDisabled}
					>
						<SelectTrigger className="w-[200px]" aria-label="Insert template">
							<SelectValue placeholder="Insert template…" />
						</SelectTrigger>
						<SelectContent>
							{Object.keys(FILE_POLICY_TEMPLATES).map((k) => (
								<SelectItem key={k} value={k}>
									{k}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					{rules.length > 0 && (
						<Select
							value={refKey}
							onValueChange={handleRef}
							disabled={mutationsDisabled}
						>
							<SelectTrigger className="w-[200px]" aria-label="Insert reference">
								<SelectValue placeholder="Insert reference…" />
							</SelectTrigger>
							<SelectContent>
								{rules.map((r) => (
									<SelectItem key={r.name} value={r.name}>
										{r.name}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					)}
				</div>
				<FilePolicyReferencePanel />
			</div>

			<JsonYamlEditor<FilePolicies>
				value={doc}
				onChange={(next) => {
					setDoc(next);
					setSaveErrors(null);
				}}
				schema={{}}
				seed={POLICY_SEED}
				defaultFormat="yaml"
				paths={paths}
				validateParsed={asFilePolicies}
				onParseErrorChange={setParseError}
				hideParseError
			/>

			{parseError && (
				<p className="text-xs text-destructive" role="alert">
					Parse error: {parseError}
				</p>
			)}

			{!parseError && saveErrors && saveErrors.length > 0 && (
				<div
					className="text-xs text-destructive space-y-0.5"
					role="alert"
					data-testid="file-policy-save-errors"
				>
					<p className="font-medium">Save errors:</p>
					{saveErrors.map((err, i) => (
						<p key={`${err.path}:${err.message}:${i}`} data-testid="file-policy-save-error">
							{err.path}: {err.message}
						</p>
					))}
				</div>
			)}

			<div className="flex items-center justify-end gap-2">
				<Button
					type="button"
					variant="outline"
					size="sm"
					onClick={() => onDelete(value)}
					disabled={!value.id}
				>
					<Trash2 className="h-4 w-4" />
					Delete
				</Button>
				<Button
					type="button"
					size="sm"
					onClick={handleSave}
					disabled={mutationsDisabled || saving}
				>
					<Save className="h-4 w-4" />
					Save policy
				</Button>
			</div>
		</section>
	);
}
