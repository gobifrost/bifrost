/**
 * Recursive graphical builder for the policy `when` AST. Each node renders
 * as a horizontal pill row:
 *
 *   [op-picker ▾] [operand …] [operand …] ... [+] [×]
 *
 * The builder produces only structurally-valid AST shapes by construction —
 * there's no escape hatch for invalid input. Final validation still happens
 * server-side (e.g. an empty `in` literal list is rejected), but the editor
 * surfaces those constraints inline so the user can fix them before saving.
 *
 * Top-level only: an "always-true" toggle that flips between `null` (no
 * filter — every caller passes) and an editable expression node.
 */

import { useId, type ReactNode } from "react";
import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";

import {
	COMPARE_OPS,
	FUNCTIONS,
	LOGIC_OPS,
	OTHER_OPS,
	USER_FIELDS,
	defaultNodeForKind,
	defaultOperandForKind,
	kindOf,
	type AllOp,
	type ExprNode,
	type FunctionName,
	type NodeKind,
	type OperandKind,
	type UserField,
} from "./expr-shapes";

const MAX_DEPTH = 8;

export interface PolicyExpressionBuilderProps {
	value: ExprNode | null;
	onChange: (next: ExprNode | null) => void;
	/**
	 * Internal — the recursive call sites bump this. The root builder leaves
	 * it undefined; that's the only place the always-true toggle renders.
	 */
	depth?: number;
}

export function PolicyExpressionBuilder({
	value,
	onChange,
	depth,
}: PolicyExpressionBuilderProps) {
	const isRoot = depth === undefined;
	const currentDepth = depth ?? 0;
	const labelId = useId();

	if (isRoot) {
		// Root tier owns the always-true toggle. Switching to "Build expression"
		// seeds the AST with a structurally-valid eq node.
		const alwaysTrue = value === null;
		return (
			<div
				className="space-y-2"
				data-testid="policy-expression-builder-root"
			>
				<div
					role="radiogroup"
					aria-labelledby={labelId}
					className="flex items-center gap-2 text-sm"
				>
					<span id={labelId} className="text-muted-foreground">
						When:
					</span>
					<Button
						type="button"
						size="sm"
						variant={alwaysTrue ? "default" : "outline"}
						role="radio"
						aria-checked={alwaysTrue}
						onClick={() => onChange(null)}
					>
						Always true
					</Button>
					<Button
						type="button"
						size="sm"
						variant={!alwaysTrue ? "default" : "outline"}
						role="radio"
						aria-checked={!alwaysTrue}
						onClick={() => {
							if (alwaysTrue) onChange(defaultNodeForKind("eq"));
						}}
					>
						Build expression
					</Button>
				</div>
				{!alwaysTrue && (
					<NodeEditor
						value={value}
						onChange={onChange}
						depth={0}
					/>
				)}
			</div>
		);
	}

	if (currentDepth > MAX_DEPTH) {
		return (
			<div className="text-xs text-muted-foreground italic">
				(deep — collapsed)
			</div>
		);
	}

	return (
		<NodeEditor
			value={value}
			onChange={onChange}
			depth={currentDepth}
		/>
	);
}

/**
 * Single AST node renderer (recursive).
 */
function NodeEditor({
	value,
	onChange,
	depth,
}: {
	value: ExprNode;
	onChange: (next: ExprNode) => void;
	depth: number;
}) {
	const kind = kindOf(value);

	function handleOpChange(nextOp: AllOp) {
		if (nextOp === kind) return;
		onChange(defaultNodeForKind(nextOp));
	}

	return (
		<div
			className="rounded-md border bg-muted/30 p-2 space-y-2"
			data-testid={`policy-expr-node-${kind}`}
			data-depth={depth}
		>
			<div className="flex items-center gap-2 flex-wrap">
				<OpPicker
					kind={kind}
					onChange={handleOpChange}
				/>
			</div>
			<NodeBody
				value={value}
				kind={kind}
				onChange={onChange}
				depth={depth}
			/>
		</div>
	);
}

/**
 * The operator (or top-level reference kind) picker. Reference kinds
 * (`row-ref` / `user-ref`) appear alongside the operators because, as the
 * spec notes, the root of a `when` can itself be a bare reference (e.g.
 * `{user: "is_platform_admin"}` for the admin-bypass template).
 */
function OpPicker({
	kind,
	onChange,
}: {
	kind: NodeKind | "unknown";
	onChange: (next: AllOp) => void;
}) {
	const safeKind: NodeKind | "unknown" = kind;
	return (
		<Select
			value={safeKind === "unknown" ? "" : safeKind}
			onValueChange={(v) => onChange(v as AllOp)}
		>
			<SelectTrigger
				className="h-7 w-[140px] text-xs"
				aria-label="Node operator"
			>
				<SelectValue placeholder="Pick…" />
			</SelectTrigger>
			<SelectContent>
				{/* References are valid root nodes too — see the admin-bypass
				    template `{user: "is_platform_admin"}`. */}
				<SelectItem value="user-ref">user.&lt;field&gt;</SelectItem>
				<SelectItem value="row-ref">row.&lt;field&gt;</SelectItem>
				{LOGIC_OPS.map((op) => (
					<SelectItem key={op} value={op}>
						{op}
					</SelectItem>
				))}
				{COMPARE_OPS.map((op) => (
					<SelectItem key={op} value={op}>
						{op}
					</SelectItem>
				))}
				{OTHER_OPS.map((op) => (
					<SelectItem key={op} value={op}>
						{op}
					</SelectItem>
				))}
			</SelectContent>
		</Select>
	);
}

function NodeBody({
	value,
	kind,
	onChange,
	depth,
}: {
	value: ExprNode;
	kind: NodeKind | "unknown";
	onChange: (next: ExprNode) => void;
	depth: number;
}) {
	switch (kind) {
		case "row-ref":
			return (
				<RowRefBody
					value={value as { row: unknown }}
					onChange={onChange}
				/>
			);
		case "user-ref":
			return (
				<UserRefBody
					value={value as { user: unknown }}
					onChange={onChange}
				/>
			);
		case "and":
		case "or":
			return (
				<LogicBody
					op={kind}
					value={value as Record<string, unknown>}
					onChange={onChange}
					depth={depth}
				/>
			);
		case "not":
			return (
				<NotBody
					value={value as { not: unknown }}
					onChange={onChange}
					depth={depth}
				/>
			);
		case "eq":
		case "neq":
		case "lt":
		case "lte":
		case "gt":
		case "gte":
			return (
				<CompareBody
					op={kind}
					value={value as Record<string, unknown>}
					onChange={onChange}
					depth={depth}
				/>
			);
		case "in":
			return (
				<InBody
					value={value as { in: unknown }}
					onChange={onChange}
					depth={depth}
				/>
			);
		case "is_null":
			return (
				<IsNullBody
					value={value as { is_null: unknown }}
					onChange={onChange}
					depth={depth}
				/>
			);
		case "call":
			return (
				<CallBody
					value={value as { call: unknown; args?: unknown }}
					onChange={onChange}
				/>
			);
		default:
			return (
				<div className="text-xs text-destructive">
					Unknown expression shape — switch to JSON to repair.
				</div>
			);
	}
}

function RowRefBody({
	value,
	onChange,
}: {
	value: { row: unknown };
	onChange: (next: ExprNode) => void;
}) {
	const text = typeof value.row === "string" ? value.row : "";
	return (
		<Input
			aria-label="Row field path"
			placeholder="created_by, organization_id, data.field..."
			className="h-7 text-xs"
			value={text}
			onChange={(e) => onChange({ row: e.target.value })}
		/>
	);
}

function UserRefBody({
	value,
	onChange,
}: {
	value: { user: unknown };
	onChange: (next: ExprNode) => void;
}) {
	const current =
		typeof value.user === "string" &&
		(USER_FIELDS as readonly string[]).includes(value.user)
			? (value.user as UserField)
			: USER_FIELDS[0];
	return (
		<Select
			value={current}
			onValueChange={(v) => onChange({ user: v as UserField })}
		>
			<SelectTrigger
				className="h-7 w-[200px] text-xs"
				aria-label="User field"
			>
				<SelectValue />
			</SelectTrigger>
			<SelectContent>
				{USER_FIELDS.map((f) => (
					<SelectItem key={f} value={f}>
						{f}
					</SelectItem>
				))}
			</SelectContent>
		</Select>
	);
}

function LogicBody({
	op,
	value,
	onChange,
	depth,
}: {
	op: "and" | "or";
	value: Record<string, unknown>;
	onChange: (next: ExprNode) => void;
	depth: number;
}) {
	const operands = Array.isArray(value[op]) ? (value[op] as ExprNode[]) : [];

	function update(nextOperands: ExprNode[]) {
		onChange({ [op]: nextOperands });
	}

	function setOperand(idx: number, next: ExprNode) {
		const copy = operands.slice();
		copy[idx] = next;
		update(copy);
	}

	function addOperand() {
		update([...operands, defaultOperandForKind("expression")]);
	}

	function removeOperand(idx: number) {
		// `and`/`or` require ≥2 operands. The per-operand [×] button is
		// disabled when removing would drop below two, but defend against
		// programmatic callers anyway: reject the removal silently.
		if (operands.length <= 2) return;
		const copy = operands.slice();
		copy.splice(idx, 1);
		update(copy);
	}

	const removeDisabled = operands.length <= 2;

	return (
		<div className="space-y-2 pl-3">
			{operands.map((operand, idx) => (
				<div
					key={idx}
					className="flex items-start gap-2"
					data-testid={`logic-operand-${idx}`}
				>
					<div className="flex-1">
						<OperandSlot
							value={operand}
							onChange={(next) => setOperand(idx, next)}
							depth={depth + 1}
						/>
					</div>
					<Button
						type="button"
						size="icon"
						variant="ghost"
						className="h-7 w-7 mt-1"
						aria-label={`Remove operand ${idx + 1}`}
						onClick={() => removeOperand(idx)}
						disabled={removeDisabled}
						title={
							removeDisabled
								? "and/or requires at least 2 operands"
								: undefined
						}
					>
						<X className="h-3.5 w-3.5" />
					</Button>
				</div>
			))}
			<Button
				type="button"
				size="sm"
				variant="outline"
				className="h-7 text-xs"
				onClick={addOperand}
			>
				<Plus className="h-3 w-3 mr-1" /> Add operand
			</Button>
		</div>
	);
}

function NotBody({
	value,
	onChange,
	depth,
}: {
	value: { not: unknown };
	onChange: (next: ExprNode) => void;
	depth: number;
}) {
	return (
		<div className="pl-3">
			<OperandSlot
				value={value.not as ExprNode}
				onChange={(next) => onChange({ not: next })}
				depth={depth + 1}
			/>
		</div>
	);
}

function CompareBody({
	op,
	value,
	onChange,
	depth,
}: {
	op: (typeof COMPARE_OPS)[number];
	value: Record<string, unknown>;
	onChange: (next: ExprNode) => void;
	depth: number;
}) {
	const args = Array.isArray(value[op]) ? (value[op] as ExprNode[]) : [];
	const left = args[0] ?? defaultOperandForKind("row-ref");
	const right = args[1] ?? defaultOperandForKind("user-ref");

	function setLeft(next: ExprNode) {
		onChange({ [op]: [next, right] });
	}
	function setRight(next: ExprNode) {
		onChange({ [op]: [left, next] });
	}

	return (
		<div className="flex items-start gap-2 pl-3 flex-wrap">
			<div className="flex-1 min-w-[200px]">
				<OperandSlot
					value={left}
					onChange={setLeft}
					depth={depth + 1}
					parentOp={op}
					testId="compare-left"
				/>
			</div>
			<div className="flex-1 min-w-[200px]">
				<OperandSlot
					value={right}
					onChange={setRight}
					depth={depth + 1}
					parentOp={op}
					testId="compare-right"
				/>
			</div>
		</div>
	);
}

function InBody({
	value,
	onChange,
	depth,
}: {
	value: { in: unknown };
	onChange: (next: ExprNode) => void;
	depth: number;
}) {
	const args = Array.isArray(value.in) ? (value.in as unknown[]) : [];
	const operand = (args[0] ?? defaultOperandForKind("row-ref")) as ExprNode;
	const list = Array.isArray(args[1]) ? (args[1] as unknown[]) : [];

	function setOperand(next: ExprNode) {
		onChange({ in: [next, list] });
	}
	function setList(next: unknown[]) {
		onChange({ in: [operand, next] });
	}

	return (
		<div className="space-y-2 pl-3">
			<OperandSlot
				value={operand}
				onChange={setOperand}
				depth={depth + 1}
			/>
			<ChipList values={list} onChange={setList} />
			{list.length === 0 && (
				<p className="text-xs text-destructive">
					in: requires a non-empty list
				</p>
			)}
		</div>
	);
}

function IsNullBody({
	value,
	onChange,
	depth,
}: {
	value: { is_null: unknown };
	onChange: (next: ExprNode) => void;
	depth: number;
}) {
	return (
		<div className="pl-3">
			<OperandSlot
				value={value.is_null as ExprNode}
				onChange={(next) => onChange({ is_null: next })}
				depth={depth + 1}
			/>
		</div>
	);
}

function CallBody({
	value,
	onChange,
}: {
	value: { call: unknown; args?: unknown };
	onChange: (next: ExprNode) => void;
}) {
	const fn =
		typeof value.call === "string" &&
		(FUNCTIONS as readonly string[]).includes(value.call)
			? (value.call as FunctionName)
			: FUNCTIONS[0];
	const args = Array.isArray(value.args) ? value.args : [];
	const arg0 = typeof args[0] === "string" ? (args[0] as string) : "";

	return (
		<div className="flex items-center gap-2 pl-3 flex-wrap">
			<Select
				value={fn}
				onValueChange={(v) =>
					onChange({ call: v as FunctionName, args: [arg0] })
				}
			>
				<SelectTrigger
					className="h-7 w-[140px] text-xs"
					aria-label="Function name"
				>
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					{FUNCTIONS.map((f) => (
						<SelectItem key={f} value={f}>
							{f}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			<Input
				aria-label="Function argument"
				className="h-7 text-xs flex-1 min-w-[160px]"
				placeholder="role name"
				value={arg0}
				onChange={(e) =>
					onChange({ call: fn, args: [e.target.value] })
				}
			/>
		</div>
	);
}

/**
 * One operand slot — kind picker + the kind-specific control. The kind
 * picker is a small dropdown; switching kind resets the slot to that kind's
 * default. `parentOp` lets sub-controls (e.g. LiteralBody) gate options
 * that would produce a structurally-invalid AST in the parent's slot —
 * specifically, hiding the `null` literal sub-kind under `eq`/`neq`.
 */
function OperandSlot({
	value,
	onChange,
	depth,
	parentOp,
	testId,
}: {
	value: ExprNode;
	onChange: (next: ExprNode) => void;
	depth: number;
	parentOp?: AllOp;
	testId?: string;
}) {
	const kind = operandKindOf(value);

	function changeKind(nextKind: OperandKind) {
		if (nextKind === kind) return;
		onChange(defaultOperandForKind(nextKind));
	}

	return (
		<div
			className="space-y-1"
			data-testid={testId ?? `operand-slot-${kind}`}
		>
			<Select
				value={kind}
				onValueChange={(v) => changeKind(v as OperandKind)}
			>
				<SelectTrigger
					className="h-7 w-[120px] text-xs"
					aria-label="Operand kind"
				>
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value="literal">Literal</SelectItem>
					<SelectItem value="user-ref">User ref</SelectItem>
					<SelectItem value="row-ref">Row ref</SelectItem>
					<SelectItem value="expression">Expression</SelectItem>
				</SelectContent>
			</Select>
			<OperandBody
				kind={kind}
				value={value}
				onChange={onChange}
				depth={depth}
				parentOp={parentOp}
			/>
		</div>
	);
}

function operandKindOf(node: ExprNode): OperandKind {
	const k = kindOf(node);
	if (k === "row-ref") return "row-ref";
	if (k === "user-ref") return "user-ref";
	if (
		k === "literal-string" ||
		k === "literal-number" ||
		k === "literal-bool" ||
		k === "literal-null"
	) {
		return "literal";
	}
	return "expression";
}

function OperandBody({
	kind,
	value,
	onChange,
	depth,
	parentOp,
}: {
	kind: OperandKind;
	value: ExprNode;
	onChange: (next: ExprNode) => void;
	depth: number;
	parentOp?: AllOp;
}) {
	if (kind === "row-ref") {
		return (
			<RowRefBody
				value={value as { row: unknown }}
				onChange={onChange}
			/>
		);
	}
	if (kind === "user-ref") {
		return (
			<UserRefBody
				value={value as { user: unknown }}
				onChange={onChange}
			/>
		);
	}
	if (kind === "literal") {
		return (
			<LiteralBody
				value={value}
				onChange={onChange}
				parentOp={parentOp}
			/>
		);
	}
	// Expression: nest a fresh builder.
	return (
		<PolicyExpressionBuilder
			value={value as ExprNode}
			onChange={(n) => onChange(n as ExprNode)}
			depth={depth}
		/>
	);
}

function LiteralBody({
	value,
	onChange,
	parentOp,
}: {
	value: ExprNode;
	onChange: (next: ExprNode) => void;
	parentOp?: AllOp;
}) {
	const sub: "string" | "number" | "boolean" | "null" =
		value === null
			? "null"
			: typeof value === "number"
				? "number"
				: typeof value === "boolean"
					? "boolean"
					: "string";

	// Validator rejects `{eq: [..., null]}` / `{neq: [..., null]}` because the
	// preferred null comparison is `is_null`. Hide the `null` sub-kind in
	// those parents so the builder can't construct an invalid AST. Other
	// parents (e.g. `is_null`'s single operand) still allow it — `null` is a
	// structurally valid scalar there.
	const allowNull = parentOp !== "eq" && parentOp !== "neq";

	function changeSub(next: typeof sub) {
		if (next === sub) return;
		switch (next) {
			case "string":
				onChange("");
				break;
			case "number":
				onChange(0);
				break;
			case "boolean":
				onChange(false);
				break;
			case "null":
				onChange(null);
				break;
		}
	}

	return (
		<div className="flex items-center gap-2 flex-wrap">
			<Select value={sub} onValueChange={(v) => changeSub(v as typeof sub)}>
				<SelectTrigger
					className="h-7 w-[100px] text-xs"
					aria-label="Literal type"
				>
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value="string">string</SelectItem>
					<SelectItem value="number">number</SelectItem>
					<SelectItem value="boolean">boolean</SelectItem>
					{allowNull && <SelectItem value="null">null</SelectItem>}
				</SelectContent>
			</Select>
			{sub === "string" && (
				<Input
					aria-label="Literal string value"
					className="h-7 text-xs flex-1 min-w-[120px]"
					value={typeof value === "string" ? value : ""}
					onChange={(e) => onChange(e.target.value)}
				/>
			)}
			{sub === "number" && (
				<Input
					aria-label="Literal number value"
					type="number"
					className="h-7 text-xs flex-1 min-w-[120px]"
					value={typeof value === "number" ? value : 0}
					onChange={(e) => {
						const n = Number(e.target.value);
						onChange(Number.isFinite(n) ? n : 0);
					}}
				/>
			)}
			{sub === "boolean" && (
				<label className="flex items-center gap-1.5 text-xs">
					<Checkbox
						checked={value === true}
						onCheckedChange={(c) => onChange(c === true)}
						aria-label="Literal boolean value"
					/>
					{value === true ? "true" : "false"}
				</label>
			)}
			{sub === "null" && (
				<span className="text-xs text-muted-foreground italic">
					null
				</span>
			)}
		</div>
	);
}

// Coerce a chip's typed text into the most specific scalar the Python
// validator accepts (str | int | float | bool | None). Order matters:
// boolean / null literals are checked first (case-insensitive exact match),
// then a strict numeric form (no scientific notation, no trailing dot).
// Anything else stays a string. Keeping this conservative — `1e5`, `1.`,
// `+42` all stay strings — avoids surprising the user in the rare cases
// where they actually want those literal characters.
function coerceChip(text: string): string | number | boolean | null {
	const lower = text.toLowerCase();
	if (lower === "true") return true;
	if (lower === "false") return false;
	if (lower === "null") return null;
	if (/^-?\d+(\.\d+)?$/.test(text)) {
		const n = Number(text);
		if (Number.isFinite(n)) return n;
	}
	return text;
}

function renderChip(v: unknown): ReactNode {
	if (v === null) {
		return <span className="text-muted-foreground italic">null</span>;
	}
	if (typeof v === "string") {
		return `"${v}"`;
	}
	// number, boolean: bare literal.
	return String(v);
}

function ChipList({
	values,
	onChange,
}: {
	values: unknown[];
	onChange: (next: unknown[]) => void;
}) {
	function addChip(text: string) {
		const trimmed = text.trim();
		if (!trimmed) return;
		onChange([...values, coerceChip(trimmed)]);
	}
	function removeChip(idx: number) {
		const copy = values.slice();
		copy.splice(idx, 1);
		onChange(copy);
	}

	return (
		<div className="flex items-center gap-1.5 flex-wrap">
			{values.map((v, idx) => (
				<span
					key={idx}
					className="inline-flex items-center gap-1 rounded-full border bg-background px-2 py-0.5 text-xs"
					data-testid={`chip-${idx}`}
				>
					{renderChip(v)}
					<button
						type="button"
						className="text-muted-foreground hover:text-destructive"
						aria-label={`Remove chip ${idx + 1}`}
						onClick={() => removeChip(idx)}
					>
						<X className="h-3 w-3" />
					</button>
				</span>
			))}
			<Input
				aria-label="Add list value"
				className="h-7 w-[200px] text-xs"
				placeholder="Enter, comma, or tab to add. Strings need no quotes; numbers, true, false, null are auto-typed."
				onKeyDown={(e) => {
					if (e.key === "Enter" || e.key === "," || e.key === "Tab") {
						e.preventDefault();
						const target = e.currentTarget;
						addChip(target.value);
						target.value = "";
					}
				}}
				onBlur={(e) => {
					if (e.currentTarget.value) {
						addChip(e.currentTarget.value);
						e.currentTarget.value = "";
					}
				}}
			/>
		</div>
	);
}
