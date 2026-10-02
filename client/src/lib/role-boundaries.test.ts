import { describe, expect, it } from "vitest";

import { placeLabel } from "./role-boundaries";

describe("placeLabel", () => {
	it("names one organization", () => {
		expect(placeLabel("organization", "Contoso")).toBe("In Contoso");
	});

	it("describes the broad boundaries in plain language", () => {
		expect(placeLabel("managed_organizations", "")).toBe(
			"In all customer organizations",
		);
		expect(placeLabel("platform", "")).toBe("Platform-wide");
	});
});
