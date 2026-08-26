import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";

export type CriteriaOperator =
	| "equals"
	| "not_equals"
	| "in"
	| "not_in"
	| "exists"
	| "not_exists"
	| "contains"
	| "starts_with"
	| "ends_with"
	| "greater_than"
	| "greater_than_or_equal"
	| "less_than"
	| "less_than_or_equal";

export type CriteriaCondition = {
	kind: "condition";
	field: string;
	operator: CriteriaOperator;
	value?: unknown;
};

export type CriteriaGroup = {
	kind: "all" | "any" | "not";
	items: CriteriaNode[];
};

export type CriteriaNode = CriteriaCondition | CriteriaGroup;

export type EventCriteria = {
	version: 1;
	root: CriteriaNode;
};

interface EventCriteriaFormProps {
	value: EventCriteria | null;
	onChange: (value: EventCriteria | null) => void;
	disabled?: boolean;
}

const OPERATOR_LABELS: Record<CriteriaOperator, string> = {
	equals: "Equals",
	not_equals: "Does not equal",
	in: "Is one of",
	not_in: "Is not one of",
	exists: "Exists",
	not_exists: "Does not exist",
	contains: "Contains",
	starts_with: "Starts with",
	ends_with: "Ends with",
	greater_than: "Greater than",
	greater_than_or_equal: "Greater than or equal",
	less_than: "Less than",
	less_than_or_equal: "Less than or equal",
};

const OPERATORS = Object.keys(OPERATOR_LABELS) as CriteriaOperator[];
const VALUELESS = new Set<CriteriaOperator>(["exists", "not_exists"]);
const NUMERIC = new Set<CriteriaOperator>([
	"greater_than",
	"greater_than_or_equal",
	"less_than",
	"less_than_or_equal",
]);
const MEMBERSHIP = new Set<CriteriaOperator>(["in", "not_in"]);

function newCondition(): CriteriaCondition {
	return {
		kind: "condition",
		field: "event.body.",
		operator: "equals",
		value: "",
	};
}

function newGroup(): CriteriaGroup {
	return { kind: "all", items: [newCondition()] };
}

function valueText(condition: CriteriaCondition): string {
	if (Array.isArray(condition.value)) return condition.value.join(", ");
	if (condition.value === null) return "null";
	return condition.value === undefined ? "" : String(condition.value);
}

function parsedValue(operator: CriteriaOperator, text: string): unknown {
	if (VALUELESS.has(operator)) return undefined;
	if (NUMERIC.has(operator)) {
		const parsed = Number(text);
		return Number.isFinite(parsed) ? parsed : text;
	}
	if (MEMBERSHIP.has(operator)) {
		return text
			.split(",")
			.map((item) => item.trim())
			.filter(Boolean);
	}
	return text;
}

function ConditionEditor({
	condition,
	onChange,
	onRemove,
	disabled,
}: {
	condition: CriteriaCondition;
	onChange: (condition: CriteriaCondition) => void;
	onRemove: () => void;
	disabled?: boolean;
}) {
	const hasValue = !VALUELESS.has(condition.operator);
	return (
		<div className="grid gap-2 rounded-md border bg-background p-3 sm:grid-cols-[minmax(10rem,1.4fr)_minmax(9rem,1fr)_minmax(8rem,1fr)_auto]">
		<div className="space-y-1">
			<Label className="text-xs">Field</Label>
			<Input
				aria-label="Criteria field"
				value={condition.field}
				onChange={(event) =>
					onChange({ ...condition, field: event.target.value })
				}
				placeholder="event.body.priority"
				disabled={disabled}
			/>
		</div>
		<div className="space-y-1">
			<Label className="text-xs">Operator</Label>
			<Select
				value={condition.operator}
				onValueChange={(value) => {
					const operator = value as CriteriaOperator;
					const next: CriteriaCondition = {
						...condition,
						operator,
					};
					if (VALUELESS.has(operator)) delete next.value;
					else if (NUMERIC.has(operator)) next.value = 0;
					else if (MEMBERSHIP.has(operator)) next.value = [];
					else if (next.value === undefined || Array.isArray(next.value)) next.value = "";
					onChange(next);
				}}
				disabled={disabled}
			>
				<SelectTrigger aria-label="Criteria operator">
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					{OPERATORS.map((operator) => (
						<SelectItem key={operator} value={operator}>
							{OPERATOR_LABELS[operator]}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
		</div>
		<div className="space-y-1">
			<Label className="text-xs">Value</Label>
			{hasValue ? (
				<Input
					aria-label="Criteria value"
					type={NUMERIC.has(condition.operator) ? "number" : "text"}
					value={valueText(condition)}
					onChange={(event) =>
						onChange({
							...condition,
							value: parsedValue(condition.operator, event.target.value),
						})
					}
					placeholder={MEMBERSHIP.has(condition.operator) ? "high, urgent" : "high"}
					disabled={disabled}
				/>
			) : (
				<div className="flex h-9 items-center text-xs text-muted-foreground">
					No value required
				</div>
			)}
		</div>
		<div className="flex items-end">
			<Button
				type="button"
				variant="ghost"
				size="icon"
				onClick={onRemove}
				disabled={disabled}
				aria-label="Remove condition"
			>
				<Trash2 className="h-4 w-4" />
			</Button>
		</div>
	</div>
	);
}

function GroupEditor({
	group,
	onChange,
	onRemove,
	depth,
	disabled,
}: {
	group: CriteriaGroup;
	onChange: (group: CriteriaGroup) => void;
	onRemove?: () => void;
	depth: number;
	disabled?: boolean;
}) {
	const replaceItem = (index: number, item: CriteriaNode) => {
		const items = [...group.items];
		items[index] = item;
		onChange({ ...group, items });
	};
	const removeItem = (index: number) => {
		onChange({ ...group, items: group.items.filter((_, i) => i !== index) });
	};

	return (
		<fieldset className="space-y-3 rounded-lg border bg-muted/20 p-3">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<div className="flex items-center gap-2">
					<Label className="text-xs">Match</Label>
					<Select
						value={group.kind}
						onValueChange={(value) => {
							const kind = value as CriteriaGroup["kind"];
							onChange({
								kind,
								items:
									kind === "not"
										? [group.items[0] ?? newCondition()]
										: group.items.length
											? group.items
											: [newCondition()],
							});
						}}
						disabled={disabled}
					>
						<SelectTrigger className="h-8 w-44" aria-label="Criteria group operator">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">All conditions</SelectItem>
							<SelectItem value="any">Any condition</SelectItem>
							<SelectItem value="not">Not</SelectItem>
						</SelectContent>
					</Select>
				</div>
				{onRemove && (
					<Button type="button" variant="ghost" size="sm" onClick={onRemove} disabled={disabled}>
						<Trash2 className="mr-1 h-3.5 w-3.5" /> Remove group
					</Button>
				)}
			</div>

			{group.items.map((item, index) =>
				item.kind === "condition" ? (
					<ConditionEditor
						key={index}
						condition={item}
						onChange={(next) => replaceItem(index, next)}
						onRemove={() => removeItem(index)}
						disabled={disabled}
					/>
				) : (
					<GroupEditor
						key={index}
						group={item}
						onChange={(next) => replaceItem(index, next)}
						onRemove={() => removeItem(index)}
						depth={depth + 1}
						disabled={disabled}
					/>
				),
			)}

			{group.items.length === 0 && (
				<p role="alert" className="text-xs text-destructive">
					Add at least one condition.
				</p>
			)}

			{group.kind !== "not" && (
				<div className="flex flex-wrap gap-2">
					<Button
						type="button"
						variant="outline"
						size="sm"
						onClick={() => onChange({ ...group, items: [...group.items, newCondition()] })}
						disabled={disabled || group.items.length >= 50}
					>
						<Plus className="mr-1 h-3.5 w-3.5" /> Condition
					</Button>
					{depth < 5 && (
						<Button
							type="button"
							variant="outline"
							size="sm"
							onClick={() => onChange({ ...group, items: [...group.items, newGroup()] })}
							disabled={disabled || group.items.length >= 50}
						>
							<Plus className="mr-1 h-3.5 w-3.5" /> Group
						</Button>
					)}
				</div>
			)}
		</fieldset>
	);
}

export function EventCriteriaForm({ value, onChange, disabled }: EventCriteriaFormProps) {
	if (!value) {
		return (
			<div className="rounded-lg border border-dashed p-4">
				<p className="text-sm font-medium">All events match this subscription</p>
				<p className="mt-1 text-xs text-muted-foreground">
					Add criteria to evaluate event fields before the target is queued.
				</p>
				<Button
					type="button"
					variant="outline"
					size="sm"
					className="mt-3"
					onClick={() => onChange({ version: 1, root: newGroup() })}
					disabled={disabled}
				>
					<Plus className="mr-1 h-3.5 w-3.5" /> Add criteria
				</Button>
			</div>
		);
	}

	const root = value.root.kind === "condition"
		? { kind: "all" as const, items: [value.root] }
		: value.root;
	return (
		<div className="space-y-2">
		<GroupEditor
			group={root}
			onChange={(next) => onChange({ version: 1, root: next })}
			depth={1}
			disabled={disabled}
		/>
		<Button type="button" variant="ghost" size="sm" onClick={() => onChange(null)} disabled={disabled}>
			Clear criteria and match all events
		</Button>
	</div>
	);
}

export function validateCriteria(criteria: EventCriteria | null): string[] {
	if (!criteria) return [];
	const errors: string[] = [];
	let nodeCount = 0;
	const visit = (node: CriteriaNode, depth: number) => {
		nodeCount += 1;
		if (depth > 5) errors.push("Criteria may not be nested more than five levels");
		if (node.kind !== "condition") {
			if (node.items.length === 0) errors.push("Every criteria group must contain at least one condition");
			if (node.kind === "not" && node.items.length !== 1) errors.push("Not groups require exactly one condition or group");
			node.items.forEach((item) => visit(item, depth + 1));
			return;
		}
		const segments = node.field.split(".");
		if (
			!(["event", "schedule"] as string[]).includes(segments[0]) ||
			segments.length < 2 ||
			segments.length > 10 ||
			segments.some((segment) => !/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/.test(segment))
		) {
			errors.push(`Invalid criteria field: ${node.field || "(empty)"}`);
		}
		if (NUMERIC.has(node.operator) && typeof node.value !== "number") {
			errors.push(`${OPERATOR_LABELS[node.operator]} requires a number`);
		}
		if (MEMBERSHIP.has(node.operator) && (!Array.isArray(node.value) || node.value.length === 0)) {
			errors.push(`${OPERATOR_LABELS[node.operator]} requires at least one comma-separated value`);
		}
	};
	visit(criteria.root, 1);
	if (nodeCount > 50) errors.push("Criteria may not contain more than 50 conditions and groups");
	return [...new Set(errors)];
}
