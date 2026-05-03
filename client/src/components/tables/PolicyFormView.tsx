/**
 * PolicyFormView — the Form tab content of PolicyEditor.
 *
 * Renders the policy list as a compact, collapsible-row UI:
 *
 *   [▾] [name] [R][C][U][D] [when summary]                       [🗑]
 *      └── (expanded) description + graphical when builder
 *
 * Owns no buffers — every keystroke flows back up to the parent through
 * `onChange`, and the parent (PolicyEditor) is responsible for the
 * cross-tab buffer reseed dance. Empty-list collapses to `null` are also
 * the parent's responsibility (PolicyEditor.emit), so we don't suppress
 * empty arrays here — we forward them.
 */

import { useId, useState } from "react";
import { ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

import { PolicyExpressionBuilder } from "./PolicyExpressionBuilder";
import { summarize, type ExprNode } from "./expr-shapes";
import type { Policy } from "./policy-templates";
import type { components } from "@/lib/v1";

type TablePolicies = components["schemas"]["TablePolicies"];

const ACTIONS = [
	{ key: "read", short: "R" },
	{ key: "create", short: "C" },
	{ key: "update", short: "U" },
	{ key: "delete", short: "D" },
] as const;

type Action = (typeof ACTIONS)[number]["key"];

export interface PolicyFormViewProps {
	value: TablePolicies | null;
	onChange: (next: TablePolicies | null) => void;
}

export function PolicyFormView({ value, onChange }: PolicyFormViewProps) {
	const policies: Policy[] = value?.policies ?? [];
	const idBase = useId();

	function emitPolicies(nextPolicies: Policy[]) {
		if (nextPolicies.length === 0) {
			onChange(null);
			return;
		}
		onChange({ policies: nextPolicies });
	}

	function updatePolicy(idx: number, next: Policy) {
		const copy = policies.slice();
		copy[idx] = next;
		emitPolicies(copy);
	}

	function removePolicy(idx: number) {
		const copy = policies.slice();
		copy.splice(idx, 1);
		emitPolicies(copy);
	}

	function addPolicy() {
		emitPolicies([
			...policies,
			{
				name: "new_policy",
				description: null,
				actions: ["read"],
				when: null,
			},
		]);
	}

	if (policies.length === 0) {
		return (
			<div className="space-y-3">
				<p className="text-sm text-muted-foreground">
					No policies. Without a policy, only the table owner and
					platform admins can access rows. Use a template or click "Add
					policy" to start.
				</p>
				<div>
					<Button
						type="button"
						variant="outline"
						size="sm"
						onClick={addPolicy}
					>
						<Plus className="h-4 w-4 mr-1" />
						Add policy
					</Button>
				</div>
			</div>
		);
	}

	return (
		<div className="space-y-1">
			<div className="divide-y rounded-md border">
				{policies.map((p, idx) => (
					<PolicyRow
						key={`${idBase}-${idx}`}
						rowKey={`${idBase}-${idx}`}
						value={p}
						onChange={(next) => updatePolicy(idx, next)}
						onRemove={() => removePolicy(idx)}
						zebra={idx % 2 === 1}
					/>
				))}
			</div>
			<div className="pt-2">
				<Button
					type="button"
					variant="outline"
					size="sm"
					onClick={addPolicy}
				>
					<Plus className="h-4 w-4 mr-1" />
					Add policy
				</Button>
			</div>
		</div>
	);
}

function PolicyRow({
	rowKey,
	value,
	onChange,
	onRemove,
	zebra,
}: {
	rowKey: string;
	value: Policy;
	onChange: (next: Policy) => void;
	onRemove: () => void;
	zebra: boolean;
}) {
	const [expanded, setExpanded] = useState(false);

	function toggleAction(action: Action, checked: boolean) {
		const next = checked
			? Array.from(new Set([...value.actions, action]))
			: value.actions.filter((a) => a !== action);
		onChange({ ...value, actions: next });
	}

	const summary = summarize(value.when as ExprNode | null);
	const summaryIsAlways = summary === "always";

	return (
		<div
			className={
				"py-1.5 px-2 " + (zebra ? "bg-muted/30" : "")
			}
			data-testid={`policy-row-${rowKey}`}
		>
			<div className="flex items-center gap-2">
				<Button
					type="button"
					size="icon"
					variant="ghost"
					className="h-6 w-6 shrink-0"
					aria-label={expanded ? "Collapse policy" : "Expand policy"}
					onClick={() => setExpanded((v) => !v)}
				>
					{expanded ? (
						<ChevronDown className="h-4 w-4" />
					) : (
						<ChevronRight className="h-4 w-4" />
					)}
				</Button>
				<Input
					aria-label={`Policy name ${rowKey}`}
					className="h-7 w-44 text-sm"
					value={value.name}
					onChange={(e) =>
						onChange({ ...value, name: e.target.value })
					}
					placeholder="policy_name"
				/>
				<div className="flex items-center gap-2 shrink-0">
					{ACTIONS.map((a) => {
						const id = `policy-action-${rowKey}-${a.key}`;
						const checked = value.actions.includes(a.key);
						return (
							<label
								key={a.key}
								htmlFor={id}
								className="flex items-center gap-1 text-xs cursor-pointer text-muted-foreground"
								title={a.key}
							>
								<Checkbox
									id={id}
									checked={checked}
									onCheckedChange={(c) =>
										toggleAction(a.key, c === true)
									}
									aria-label={a.key}
								/>
								{a.short}
							</label>
						);
					})}
				</div>
				<div
					className={
						"flex-1 min-w-0 truncate text-xs " +
						(summaryIsAlways
							? "text-muted-foreground italic"
							: "text-foreground")
					}
					data-testid={`policy-summary-${rowKey}`}
					title={summary}
				>
					{summary}
				</div>
				<Button
					type="button"
					size="icon"
					variant="ghost"
					className="h-7 w-7 shrink-0"
					aria-label={`Remove policy ${value.name || rowKey}`}
					onClick={onRemove}
				>
					<Trash2 className="h-4 w-4" />
				</Button>
			</div>
			{expanded && (
				<div
					className="pl-8 pt-2 pb-1 space-y-3"
					data-testid={`policy-row-expanded-${rowKey}`}
				>
					<Input
						aria-label={`Policy description ${rowKey}`}
						className="h-8 text-sm"
						value={value.description ?? ""}
						onChange={(e) =>
							onChange({
								...value,
								description: e.target.value || null,
							})
						}
						placeholder="Description (optional)"
					/>
					<PolicyExpressionBuilder
						value={(value.when ?? null) as ExprNode | null}
						onChange={(next) =>
							onChange({
								...value,
								when: next as Policy["when"],
							})
						}
					/>
				</div>
			)}
		</div>
	);
}
