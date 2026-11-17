import { describe, expect, it } from "vitest";
import { computeAutoMatches, exactMatch, fuzzyMatch } from "./matching";

const entities = [
	{ value: "northwind", label: "Northwind Traders" },
	{ value: "contoso", label: "Contoso Dental" },
];

describe("integration matching", () => {
	it("matches exact names regardless of case and surrounding whitespace", () => {
		expect(exactMatch(" NORTHWIND TRADERS ", entities)).toEqual({
			entityId: "northwind",
			entityName: "Northwind Traders",
		});
		expect(exactMatch("Northwind Trade", entities)).toBeNull();
	});

	it("finds a close customer name with fuzzy matching", () => {
		expect(fuzzyMatch("Contoso Denta", entities)).toMatchObject({
			entityId: "contoso",
			entityName: "Contoso Dental",
		});
		expect(fuzzyMatch("Northwind", [])).toBeNull();
	});

	it("scores exact names 100 and close names by confidence", () => {
		const result = computeAutoMatches(
			[
				{ id: "exact", name: "northwind traders" },
				{ id: "close", name: "Contoso Denta" },
			],
			entities,
			new Set(),
			new Set(),
		);
		expect(result.suggestions).toEqual([
			expect.objectContaining({
				organizationId: "exact",
				entityId: "northwind",
				score: 100,
			}),
			expect.objectContaining({
				organizationId: "close",
				entityId: "contoso",
			}),
		]);
		expect(result.suggestions[1].score).toBeLessThan(100);
	});

	it("excludes mapped organizations and already-used entities", () => {
		const result = computeAutoMatches(
			[
				{ id: "mapped", name: "Contoso Dental" },
				{ id: "taken", name: "Northwind Traders" },
				{ id: "available", name: "Contoso Dental" },
			],
			entities,
			new Set(["mapped"]),
			new Set(["northwind"]),
		);
		expect(result.suggestions).toEqual([
			expect.objectContaining({
				organizationId: "available",
				entityId: "contoso",
			}),
		]);
		expect(result.unmatchedOrgIds).toEqual(["taken"]);
		expect(result.stats).toMatchObject({ total: 2, matched: 1 });
	});
});
