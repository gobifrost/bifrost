import { describe, expect, it } from "vitest";

import { collectOrganizationTags, matchesTagFilter } from "./organization-tags";

describe("collectOrganizationTags", () => {
	it("returns distinct tags case-insensitively, sorted, keeping the first spelling", () => {
		expect(
			collectOrganizationTags([
				{ tags: ["MSP", "connectivity"] },
				{ tags: ["msp", "Licence"] },
				{ tags: null },
				{},
			]),
		).toEqual(["connectivity", "Licence", "MSP"]);
	});
});

describe("matchesTagFilter", () => {
	it("matches everything when no tag is selected", () => {
		expect(matchesTagFilter({ tags: [] }, [])).toBe(true);
	});

	it("matches an item carrying any selected tag, ignoring case", () => {
		expect(matchesTagFilter({ tags: ["MSP"] }, ["msp", "Licence"])).toBe(
			true,
		);
		expect(matchesTagFilter({ tags: ["Connectivity"] }, ["MSP"])).toBe(
			false,
		);
		expect(matchesTagFilter({}, ["MSP"])).toBe(false);
	});
});
