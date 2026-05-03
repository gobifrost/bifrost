/**
 * Unit tests for the AST helpers backing the Form-tab `when` builder.
 */

import { describe, it, expect } from "vitest";
import {
	defaultNodeForKind,
	kindOf,
	summarize,
	type NodeKind,
} from "./expr-shapes";

describe("kindOf", () => {
	it.each<[unknown, NodeKind | "unknown"]>([
		[null, "literal-null"],
		["hello", "literal-string"],
		[42, "literal-number"],
		[true, "literal-bool"],
		[{ row: "created_by" }, "row-ref"],
		[{ user: "user_id" }, "user-ref"],
		[{ and: [1, 2] }, "and"],
		[{ or: [1, 2] }, "or"],
		[{ not: { row: "x" } }, "not"],
		[{ eq: [1, 2] }, "eq"],
		[{ neq: [1, 2] }, "neq"],
		[{ lt: [1, 2] }, "lt"],
		[{ lte: [1, 2] }, "lte"],
		[{ gt: [1, 2] }, "gt"],
		[{ gte: [1, 2] }, "gte"],
		[{ in: [{ row: "x" }, ["a", "b"]] }, "in"],
		[{ is_null: { row: "x" } }, "is_null"],
		[{ call: "has_role", args: ["admin"] }, "call"],
		[{}, "unknown"],
		[[1, 2, 3], "unknown"],
	])("kindOf(%s) === %s", (input, expected) => {
		expect(kindOf(input)).toBe(expected);
	});
});

describe("defaultNodeForKind round-trip", () => {
	const kinds: NodeKind[] = [
		"literal-string",
		"literal-number",
		"literal-bool",
		"literal-null",
		"row-ref",
		"user-ref",
		"and",
		"or",
		"not",
		"eq",
		"neq",
		"lt",
		"lte",
		"gt",
		"gte",
		"in",
		"is_null",
		"call",
	];
	it.each(kinds)("kindOf(defaultNodeForKind(%s)) === %s", (k) => {
		expect(kindOf(defaultNodeForKind(k))).toBe(k);
	});

	it("comparison default uses row/user-refs (not raw nulls — validator rejects those)", () => {
		expect(defaultNodeForKind("eq")).toEqual({
			eq: [{ row: "" }, { user: "user_id" }],
		});
	});
});

describe("summarize", () => {
	it("null → 'always'", () => {
		expect(summarize(null)).toBe("always");
	});

	it("user ref", () => {
		expect(summarize({ user: "is_platform_admin" })).toBe(
			"user.is_platform_admin",
		);
	});

	it("row ref", () => {
		expect(summarize({ row: "data.x" })).toBe("row.data.x");
	});

	it("eq with refs", () => {
		expect(
			summarize({ eq: [{ row: "created_by" }, { user: "user_id" }] }),
		).toBe("row.created_by = user.user_id");
	});

	it("neq lt lte gt gte symbols", () => {
		expect(summarize({ neq: [1, 2] })).toBe("1 ≠ 2");
		expect(summarize({ lt: [1, 2] })).toBe("1 < 2");
		expect(summarize({ lte: [1, 2] })).toBe("1 ≤ 2");
		expect(summarize({ gt: [1, 2] })).toBe("1 > 2");
		expect(summarize({ gte: [1, 2] })).toBe("1 ≥ 2");
	});

	it("and/or preserve order", () => {
		expect(
			summarize({
				and: [{ row: "a" }, { row: "b" }, { row: "c" }],
			}),
		).toBe("row.a AND row.b AND row.c");
		expect(
			summarize({
				or: [{ row: "a" }, { row: "b" }],
			}),
		).toBe("row.a OR row.b");
	});

	it("not", () => {
		expect(summarize({ not: { user: "is_platform_admin" } })).toBe(
			"NOT user.is_platform_admin",
		);
	});

	it("in renders the literal list", () => {
		expect(
			summarize({
				in: [{ row: "status" }, ["open", "in_progress"]],
			}),
		).toBe("row.status in [open, in_progress]");
	});

	it("is_null", () => {
		expect(summarize({ is_null: { row: "deleted_at" } })).toBe(
			"row.deleted_at is null",
		);
	});

	it("call has_role", () => {
		expect(summarize({ call: "has_role", args: ["admin"] })).toBe(
			"has_role('admin')",
		);
	});

	it("string literal is quoted", () => {
		expect(summarize("x")).toBe('"x"');
	});

	it("number / bool literals", () => {
		expect(summarize(7)).toBe("7");
		expect(summarize(false)).toBe("false");
	});

	it("falls back to <expr> on unknown shapes", () => {
		expect(summarize({} as unknown)).toBe("<expr>");
	});
});
