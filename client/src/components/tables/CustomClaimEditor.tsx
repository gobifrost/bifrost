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
	onSave: (value: CustomClaim) => void;
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
	const queryValid = isClaimQuery(value.query);

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between gap-3">
				<div>
					<h3 className="text-sm font-medium">Custom Claim</h3>
					<p className="text-xs text-muted-foreground">
						Resolved once per request and available to table policies.
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
					disabled={nameDisabled}
					onChange={(event) =>
						onChange({ ...value, name: event.target.value })
					}
				/>
			</div>

			<div className="grid gap-2">
				<Label htmlFor="claim-description">Description</Label>
				<Input
					id="claim-description"
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
					value={value.type}
					onChange={(event) =>
						onChange({
							...value,
							type: event.target.value as CustomClaim["type"],
						})
					}
					className="h-9 rounded-md border border-input bg-background px-3 text-sm"
				>
					<option value="list">list</option>
					<option value="scalar">scalar</option>
				</select>
			</div>

			<div className="grid gap-2">
				<Label>Query</Label>
				<JsonYamlEditor<ClaimQuery>
					value={isClaimQuery(value.query) ? value.query : null}
					onChange={(query) => {
						if (!query) return;
						onChange({ ...value, query });
					}}
					schema={CLAIM_QUERY_SCHEMA}
					seed={CLAIM_QUERY_SEED}
					validateParsed={asClaimQuery}
					paths={{
						json: "claim-query.json",
						yaml: "claim-query.yaml",
					}}
				/>
			</div>

			<div className="flex justify-end gap-2">
				<Button type="button" variant="ghost" onClick={onCancel}>
					<X className="h-4 w-4" />
					Cancel
				</Button>
				<Button
					type="button"
					disabled={!queryValid}
					onClick={() => onSave(value)}
				>
					<Save className="h-4 w-4" />
					Save
				</Button>
			</div>
		</div>
	);
}
