/**
 * Pure helpers for the Form tab's graphical `when` AST builder.
 *
 * The policy schema's AST is a single-key node — see policy-schema.json — but
 * its operand union (literal | reference | nested expression) is too dynamic
 * to express usefully in TypeScript. We type the builder boundaries as
 * `unknown` and narrow with the helpers below; consumers cast through `as`
 * at the leaves rather than spraying `any`.
 *
 * The catalog constants (USER_FIELDS, *_OPS, FUNCTIONS) are mirrored from
 * api/shared/policies/* — keep them in sync when the server gains a new
 * operator or function.
 */

export type ExprNode = unknown;

export const USER_FIELDS = [
	"user_id",
	"email",
	"organization_id",
	"is_platform_admin",
	"role_ids",
	"role_names",
] as const;
export type UserField = (typeof USER_FIELDS)[number];

export const LOGIC_OPS = ["and", "or", "not"] as const;
export const COMPARE_OPS = ["eq", "neq", "lt", "lte", "gt", "gte"] as const;
export const OTHER_OPS = ["in", "is_null", "call"] as const;
export const ALL_OPS = [...LOGIC_OPS, ...COMPARE_OPS, ...OTHER_OPS] as const;
export type AllOp = (typeof ALL_OPS)[number];

// Mirrors api/shared/policies/functions.py.
export const FUNCTIONS = ["has_role"] as const;
export type FunctionName = (typeof FUNCTIONS)[number];

export type NodeKind =
	| "literal-string"
	| "literal-number"
	| "literal-bool"
	| "literal-null"
	| "row-ref"
	| "user-ref"
	| "and"
	| "or"
	| "not"
	| "eq"
	| "neq"
	| "lt"
	| "lte"
	| "gt"
	| "gte"
	| "in"
	| "is_null"
	| "call";

export type OperandKind = "literal" | "row-ref" | "user-ref" | "expression";

export const COMPARE_SYMBOL: Record<(typeof COMPARE_OPS)[number], string> = {
	eq: "=",
	neq: "≠",
	lt: "<",
	lte: "≤",
	gt: ">",
	gte: "≥",
};

function isPlainObject(node: ExprNode): node is Record<string, unknown> {
	return (
		typeof node === "object" &&
		node !== null &&
		!Array.isArray(node)
	);
}

/**
 * Identify the AST shape of `node`. Returns "unknown" if the node doesn't
 * match any known kind (typically because the user is mid-edit through a
 * code tab).
 */
export function kindOf(node: ExprNode): NodeKind | "unknown" {
	if (node === null) return "literal-null";
	if (typeof node === "string") return "literal-string";
	if (typeof node === "number") return "literal-number";
	if (typeof node === "boolean") return "literal-bool";
	if (!isPlainObject(node)) return "unknown";
	const keys = Object.keys(node);
	if (keys.length === 0) return "unknown";
	if (keys.includes("row")) return "row-ref";
	if (keys.includes("user")) return "user-ref";
	if (keys.includes("call")) return "call";
	// Single-key operator nodes.
	for (const op of ALL_OPS) {
		if (Object.prototype.hasOwnProperty.call(node, op)) {
			return op;
		}
	}
	return "unknown";
}

/**
 * Return a structurally-valid default node for the given kind. The validator
 * still has to pass downstream — e.g. an `eq` node with two unset operands
 * would be rejected — but the shape is right and the editor can render it.
 */
export function defaultNodeForKind(kind: NodeKind): ExprNode {
	switch (kind) {
		case "literal-string":
			return "";
		case "literal-number":
			return 0;
		case "literal-bool":
			return false;
		case "literal-null":
			return null;
		case "row-ref":
			return { row: "" };
		case "user-ref":
			return { user: USER_FIELDS[0] };
		case "and":
			return {
				and: [
					{ row: "" },
					{ user: USER_FIELDS[0] },
				],
			};
		case "or":
			return {
				or: [
					{ row: "" },
					{ user: USER_FIELDS[0] },
				],
			};
		case "not":
			return { not: { row: "" } };
		case "eq":
		case "neq":
		case "lt":
		case "lte":
		case "gt":
		case "gte":
			// Use row/user-ref — the validator rejects `null` literals here,
			// and an empty-string row-ref keeps the AST shape valid until the
			// user fills the column name in.
			return { [kind]: [{ row: "" }, { user: USER_FIELDS[0] }] };
		case "in":
			return { in: [{ row: "" }, []] };
		case "is_null":
			return { is_null: { row: "" } };
		case "call":
			return { call: FUNCTIONS[0], args: [""] };
	}
}

export function defaultOperandForKind(kind: OperandKind): ExprNode {
	switch (kind) {
		case "literal":
			return "";
		case "row-ref":
			return { row: "" };
		case "user-ref":
			return { user: USER_FIELDS[0] };
		case "expression":
			// The structurally-valid `eq` default lets the user start composing
			// without immediately tripping the validator on `null` operands.
			return defaultNodeForKind("eq");
	}
}

/**
 * One-line preview for a collapsed policy row. Pragmatic — not a pretty
 * printer; if the input doesn't match a known shape we fall back to
 * `<expr>`. `null` (and the literal absence of a `when`) renders as
 * `"always"`.
 */
export function summarize(expr: ExprNode | null): string {
	if (expr === null || expr === undefined) return "always";
	const kind = kindOf(expr);
	switch (kind) {
		case "literal-string":
			return JSON.stringify(expr as string);
		case "literal-number":
			return String(expr as number);
		case "literal-bool":
			return String(expr as boolean);
		case "literal-null":
			return "null";
		case "row-ref": {
			const path = (expr as { row: unknown }).row;
			return `row.${typeof path === "string" ? path : "?"}`;
		}
		case "user-ref": {
			const f = (expr as { user: unknown }).user;
			return `user.${typeof f === "string" ? f : "?"}`;
		}
		case "and": {
			const xs = (expr as { and: unknown }).and;
			if (!Array.isArray(xs)) return "<expr>";
			return xs.map((x) => summarize(x as ExprNode)).join(" AND ");
		}
		case "or": {
			const xs = (expr as { or: unknown }).or;
			if (!Array.isArray(xs)) return "<expr>";
			return xs.map((x) => summarize(x as ExprNode)).join(" OR ");
		}
		case "not": {
			const inner = (expr as { not: unknown }).not;
			return `NOT ${summarize(inner as ExprNode)}`;
		}
		case "eq":
		case "neq":
		case "lt":
		case "lte":
		case "gt":
		case "gte": {
			const sym = COMPARE_SYMBOL[kind];
			const args = (expr as Record<string, unknown>)[kind];
			if (!Array.isArray(args) || args.length !== 2) return "<expr>";
			return `${summarize(args[0] as ExprNode)} ${sym} ${summarize(
				args[1] as ExprNode,
			)}`;
		}
		case "in": {
			const args = (expr as { in: unknown }).in;
			if (!Array.isArray(args) || args.length !== 2) return "<expr>";
			const list = args[1];
			const listText = Array.isArray(list)
				? list
						.map((v) => {
							if (typeof v === "string") return v;
							return summarize(v as ExprNode);
						})
						.join(", ")
				: "?";
			return `${summarize(args[0] as ExprNode)} in [${listText}]`;
		}
		case "is_null": {
			const inner = (expr as { is_null: unknown }).is_null;
			return `${summarize(inner as ExprNode)} is null`;
		}
		case "call": {
			const fn = (expr as { call: unknown }).call;
			const args = (expr as { args?: unknown }).args;
			const fnText = typeof fn === "string" ? fn : "?";
			const argsText = Array.isArray(args)
				? args
						.map((a) => {
							if (typeof a === "string") return `'${a}'`;
							return summarize(a as ExprNode);
						})
						.join(", ")
				: "";
			return `${fnText}(${argsText})`;
		}
		default:
			return "<expr>";
	}
}
