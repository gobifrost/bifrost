/**
 * Unit tests for the Pretty View shape-classification heuristics.
 *
 * Covers every rung of the rendering ladder plus the caps: uniform vs ragged
 * arrays, depth limits, key/row/column caps, empty array/object, nulls, and
 * mixed types.
 */

import { describe, it, expect } from "vitest";
import {
	classify,
	tableColumns,
	isScalar,
	MAX_OBJECT_DEPTH,
	MAX_OBJECT_KEYS,
	MAX_TABLE_ROWS,
	MAX_TABLE_COLUMNS,
	MAX_SCALAR_ARRAY_ITEMS,
} from "./prettyShape";

describe("isScalar", () => {
	it("treats null, undefined, strings, numbers and booleans as scalar", () => {
		expect(isScalar(null)).toBe(true);
		expect(isScalar(undefined)).toBe(true);
		expect(isScalar("x")).toBe(true);
		expect(isScalar("")).toBe(true);
		expect(isScalar(0)).toBe(true);
		expect(isScalar(NaN)).toBe(true);
		expect(isScalar(false)).toBe(true);
	});

	it("treats objects and arrays as non-scalar", () => {
		expect(isScalar({})).toBe(false);
		expect(isScalar([])).toBe(false);
		expect(isScalar({ a: 1 })).toBe(false);
	});
});

describe("classify — scalars", () => {
	it("classifies every scalar as 'scalar'", () => {
		expect(classify("hello")).toBe("scalar");
		expect(classify(42)).toBe("scalar");
		expect(classify(true)).toBe("scalar");
		expect(classify(null)).toBe("scalar");
		expect(classify(undefined)).toBe("scalar");
	});
});

describe("classify — flat objects", () => {
	it("classifies an all-scalar object as flat-object", () => {
		expect(classify({ first_name: "Ada", age: 36, active: true })).toBe(
			"flat-object",
		);
	});

	it("classifies an empty object as flat-object (rendered as empty rows)", () => {
		expect(classify({})).toBe("flat-object");
	});

	it("allows null values inside a flat object", () => {
		expect(classify({ a: null, b: "x" })).toBe("flat-object");
	});

	it("allows nested objects up to the depth budget", () => {
		// depth 0 → 1 → 2 of nested rows is within MAX_OBJECT_DEPTH (3)
		const nested = { a: { b: { c: "leaf" } } };
		expect(classify(nested)).toBe("flat-object");
	});

	it("falls back to json when nesting exceeds the depth budget", () => {
		// 4 object levels under the entry value → the innermost classifies at
		// depth MAX_OBJECT_DEPTH and poisons the whole branch.
		const tooDeep = { a: { b: { c: { d: "leaf" } } } };
		expect(classify(tooDeep)).toBe("json");
	});

	it("respects an explicit depth offset", () => {
		const twoLevels = { a: { b: "leaf" } };
		expect(classify(twoLevels, MAX_OBJECT_DEPTH - 2)).toBe("flat-object");
		expect(classify(twoLevels, MAX_OBJECT_DEPTH - 1)).toBe("json");
		expect(classify({ a: 1 }, MAX_OBJECT_DEPTH)).toBe("json");
	});

	it("allows scalar arrays and mini tables as nested values", () => {
		expect(
			classify({
				tags: ["a", "b"],
				rows: [
					{ label: "x", value: 1 },
					{ label: "y", value: 2 },
				],
			}),
		).toBe("flat-object");
	});

	it("falls back to json when any branch is unrenderable", () => {
		// The ragged array poisons the whole object — rule 5, all-or-nothing.
		expect(
			classify({ name: "x", junk: [{ a: 1 }, "not-an-object"] }),
		).toBe("json");
	});

	it("falls back to json beyond the key cap", () => {
		const wide: Record<string, number> = {};
		for (let i = 0; i < MAX_OBJECT_KEYS; i++) wide[`k${i}`] = i;
		expect(classify(wide)).toBe("flat-object");
		wide.one_more = 99;
		expect(classify(wide)).toBe("json");
	});
});

describe("classify — scalar arrays", () => {
	it("classifies an all-scalar array as scalar-array", () => {
		expect(classify(["a", "b", "c"])).toBe("scalar-array");
		expect(classify([1, 2, 3])).toBe("scalar-array");
		expect(classify([true, null, "mixed scalars", 4])).toBe("scalar-array");
	});

	it("classifies an empty array as scalar-array", () => {
		expect(classify([])).toBe("scalar-array");
	});

	it("falls back to json beyond the scalar-array length cap", () => {
		const long = Array.from({ length: MAX_SCALAR_ARRAY_ITEMS }, (_, i) => i);
		expect(classify(long)).toBe("scalar-array");
		expect(classify([...long, 99])).toBe("json");
	});
});

describe("classify — object tables", () => {
	const licenseReport = [
		{ label: "Microsoft 365 Business Premium", value: "m365_business_premium" },
		{ label: "Microsoft 365 E3", value: "m365_e3" },
		{ label: "Defender for Endpoint P2", value: "defender_p2" },
		{ label: "Acronis Cyber Protect Advanced", value: "acronis_advanced" },
		{ label: "Huntress Managed EDR", value: "huntress_edr" },
	];

	it("classifies a uniform array of flat objects as object-table", () => {
		expect(classify(licenseReport)).toBe("object-table");
	});

	it("classifies a single-item array of a flat object as object-table", () => {
		expect(classify([{ label: "Only", value: 1 }])).toBe("object-table");
	});

	it("falls back to json when any cell is non-scalar", () => {
		expect(
			classify([
				{ label: "a", value: 1 },
				{ label: "b", value: { nested: true } },
			]),
		).toBe("json");
	});

	it("falls back to json when items are not all objects", () => {
		expect(classify([{ a: 1 }, "loose string"])).toBe("json");
		expect(classify([{ a: 1 }, [1, 2]])).toBe("json");
	});

	it("falls back to json for ragged shapes below the coverage bar", () => {
		// 'extra' appears on 1 of 5 rows (20% < 80%).
		expect(
			classify([
				{ a: 1 },
				{ a: 2 },
				{ a: 3 },
				{ a: 4 },
				{ a: 5, extra: "rare" },
			]),
		).toBe("json");
	});

	it("tolerates a mostly-shared key set (≥80% coverage)", () => {
		// 'b' appears on 4 of 5 rows (80%).
		expect(
			classify([
				{ a: 1, b: 1 },
				{ a: 2, b: 2 },
				{ a: 3, b: 3 },
				{ a: 4, b: 4 },
				{ a: 5 },
			]),
		).toBe("object-table");
	});

	it("keeps large flat arrays table-shaped so the renderer can preview them", () => {
		const make = (n: number) =>
			Array.from({ length: n }, (_, i) => ({ label: `r${i}`, value: i }));
		expect(classify(make(MAX_TABLE_ROWS))).toBe("object-table");
		expect(classify(make(5395))).toBe("object-table");
	});

	it("falls back to json beyond the column cap", () => {
		const wideRow: Record<string, number> = {};
		for (let i = 0; i < MAX_TABLE_COLUMNS + 1; i++) wideRow[`c${i}`] = i;
		expect(classify([wideRow, { ...wideRow }])).toBe("json");
		const okRow: Record<string, number> = {};
		for (let i = 0; i < MAX_TABLE_COLUMNS; i++) okRow[`c${i}`] = i;
		expect(classify([okRow, { ...okRow }])).toBe("object-table");
	});

	it("falls back to json when items have no keys", () => {
		expect(classify([{}, {}])).toBe("json");
	});
});

describe("tableColumns", () => {
	it("returns columns in first-seen key order", () => {
		expect(
			tableColumns([
				{ label: "a", value: 1 },
				{ value: 2, label: "b", note: "x" },
				{ label: "c", value: 3, note: "y" },
				{ label: "d", value: 4, note: "z" },
				{ label: "e", value: 5, note: "w" },
			]),
		).toEqual(["label", "value", "note"]);
	});

	it("returns null for non-arrays, empty arrays and non-table shapes", () => {
		expect(tableColumns({ a: 1 })).toBeNull();
		expect(tableColumns("nope")).toBeNull();
		expect(tableColumns([])).toBeNull();
		expect(tableColumns([1, 2, 3])).toBeNull();
		expect(tableColumns([{ a: { deep: true } }])).toBeNull();
	});

	it("returns null when coverage falls below the bar", () => {
		expect(
			tableColumns([
				{ a: 1 },
				{ a: 2 },
				{ a: 3 },
				{ a: 4 },
				{ b: 5 },
			]),
		).toBeNull();
	});
});
