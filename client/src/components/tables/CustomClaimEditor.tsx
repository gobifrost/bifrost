import { useEffect, useRef, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Save, X } from "lucide-react";

import { HelpSlideout } from "@/components/shared/HelpSlideout";
import { JsonYamlEditor } from "@/components/shared/JsonYamlEditor";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ClaimReferenceContent } from "./ClaimReferenceContent";
import type { components } from "@/lib/v1";
import type { CustomClaim } from "@/services/claims";

type ClaimQuery = components["schemas"]["ClaimQuery"];

const CLAIM_QUERY_SCHEMA = {
	type: "object",
	required: ["table", "select"],
	properties: {
		table: { type: "string" },
		where: { type: ["object", "null"] },
		select: { type: "string" },
	},
	additionalProperties: false,
};

const CLAIM_QUERY_SEED: ClaimQuery = { table: "", select: "" };

export interface CustomClaimEditorProps {
	value: CustomClaim;
	onChange: (next: CustomClaim) => void;
	onSave: (value: CustomClaim) => void | Promise<void>;
	onCancel: () => void;
	nameDisabled?: boolean;
}

function isClaimQuery(value: unknown): value is ClaimQuery {
	if (!value || typeof value !== "object" || Array.isArray(value)) {
		return false;
	}
	const query = value as Record<string, unknown>;
	return (
		typeof query.table === "string" &&
		query.table.trim().length > 0 &&
		typeof query.select === "string" &&
		query.select.trim().length > 0
	);
}

function asClaimQuery(value: unknown): ClaimQuery {
	if (!isClaimQuery(value)) {
		throw new Error("Query must include non-empty `table` and `select`.");
	}
	return value;
}

export function CustomClaimEditor({
	value,
	onChange,
	onSave,
	onCancel,
	nameDisabled = false,
}: CustomClaimEditorProps) {
	const busy = useRef(false);
	const saveErrorRef = useRef<HTMLDivElement>(null);
	const [parseError, setParseError] = useState<string | null>(null);
	const [queryEmpty, setQueryEmpty] = useState(false);
	const [isSaving, setIsSaving] = useState(false);
	const [saveError, setSaveError] = useState(false);
	useEffect(() => {
		if (saveError) {
			saveErrorRef.current?.focus();
			saveErrorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [saveError]);
	const queryValid = isClaimQuery(value.query) && !parseError && !queryEmpty;
	async function save() {
		if (busy.current || !queryValid || !value.name.trim()) return;
		busy.current = true;
		setIsSaving(true);
		setSaveError(false);
		try {
			await onSave(value);
		} catch {
			setSaveError(true);
		} finally {
			busy.current = false;
			setIsSaving(false);
		}
	}

	return (
		<div className="min-w-0 space-y-6" aria-busy={isSaving}>
			<div className="flex items-center justify-between gap-3">
				<div>
					<h2 className="font-display text-xl font-semibold">
						{nameDisabled
							? "Edit custom claim"
							: "New custom claim"}
					</h2>
					<p className="mt-1 text-sm leading-6 text-muted-foreground">
						Resolved once per request and available to table
						policies.
					</p>
				</div>
				<HelpSlideout title="Custom Claims reference">
					<ClaimReferenceContent />
				</HelpSlideout>
			</div>

			<div className="grid gap-2">
				<Label htmlFor="claim-name">Name</Label>
				<Input
					id="claim-name"
					value={value.name}
					disabled={nameDisabled || isSaving}
					className="h-11 font-mono"
					onChange={(event) =>
						onChange({ ...value, name: event.target.value })
					}
				/>
			</div>

			<div className="grid gap-2">
				<Label htmlFor="claim-description">Description</Label>
				<Input
					id="claim-description"
					disabled={isSaving}
					className="h-11"
					value={value.description ?? ""}
					onChange={(event) =>
						onChange({
							...value,
							description: event.target.value,
						})
					}
				/>
			</div>

			<div className="grid gap-2">
				<Label htmlFor="claim-type">Type</Label>
				<select
					id="claim-type"
					disabled={isSaving}
					value={value.type}
					onChange={(event) =>
						onChange({
							...value,
							type: event.target.value as CustomClaim["type"],
						})
					}
					className="h-11 w-full rounded-[var(--bf-radius-control)] border border-border/70 bg-background px-3 text-sm transition-colors duration-[var(--bf-motion-feedback)] outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none"
				>
					<option value="list">list</option>
					<option value="scalar">scalar</option>
				</select>
			</div>

			<div className="grid gap-2">
				<Label>Query</Label>
				<JsonYamlEditor<ClaimQuery>
					value={
						!queryEmpty && isClaimQuery(value.query)
							? value.query
							: null
					}
					onChange={(query) => {
						setQueryEmpty(!query);
						if (!query) return;
						onChange({ ...value, query });
					}}
					readOnly={isSaving}
					onParseErrorChange={setParseError}
					schema={CLAIM_QUERY_SCHEMA}
					seed={CLAIM_QUERY_SEED}
					validateParsed={asClaimQuery}
					paths={{
						json: "claim-query.json",
						yaml: "claim-query.yaml",
					}}
				/>
			</div>

			{saveError && (
				<Alert
					variant="destructive"
					ref={saveErrorRef}
					tabIndex={-1}
					className="scroll-mb-24 outline-none"
				>
					<AlertTitle>Claim could not be saved</AlertTitle>
					<AlertDescription>
						Your changes are preserved. Try saving again.
					</AlertDescription>
				</Alert>
			)}
			<div className="flex flex-wrap justify-end gap-2 border-t pt-4">
				<Button
					type="button"
					variant="outline"
					className="min-h-11"
					disabled={isSaving}
					onClick={onCancel}
				>
					<X className="h-4 w-4" />
					Cancel
				</Button>
				<Button
					type="button"
					className="min-h-11"
					disabled={isSaving || !queryValid || !value.name.trim()}
					aria-label={isSaving ? "Saving…" : "Save"}
					onClick={save}
				>
					<Save className="h-4 w-4" />
					{isSaving ? <span role="status">Saving…</span> : "Save"}
				</Button>
			</div>
		</div>
	);
}
