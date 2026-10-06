import { describe, expect, it } from "vitest";

import {
	offeredPresets,
	placeLabel,
	placesForPreset,
	presetFor,
	type PresetRole,
	type RolePlace,
} from "./role-boundaries";

const PROVIDER = "org-provider";

const everywhere: PresetRole = {
	boundary_kinds: ["organization", "managed_organizations", "platform"],
	provider_organization_allowed: true,
};
const operator: PresetRole = {
	boundary_kinds: ["organization", "managed_organizations"],
	provider_organization_allowed: false,
};
const orgOnly: PresetRole = {
	boundary_kinds: ["organization"],
	provider_organization_allowed: true,
};

const org = (id: string): RolePlace => ({
	kind: "organization",
	organization_id: id,
});
const managed: RolePlace = {
	kind: "managed_organizations",
	organization_id: null,
};
const platform: RolePlace = { kind: "platform", organization_id: null };

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

describe("offeredPresets", () => {
	it("offers All organizations only when all three kinds and the provider org are allowed", () => {
		expect(offeredPresets(everywhere)).toEqual([
			"selected",
			"customers",
			"all",
		]);
		expect(offeredPresets(operator)).toEqual(["selected", "customers"]);
		expect(
			offeredPresets({
				...everywhere,
				provider_organization_allowed: false,
			}),
		).toEqual(["selected", "customers"]);
	});

	it("offers only Selected organizations when the server allows only organizations", () => {
		expect(offeredPresets(orgOnly)).toEqual(["selected"]);
	});

	it("offers nothing for a role placed only platform-wide", () => {
		expect(
			offeredPresets({
				boundary_kinds: ["platform"],
				provider_organization_allowed: true,
			}),
		).toEqual([]);
	});
});

describe("presetFor", () => {
	it("recognises each preset's places in any order", () => {
		expect(presetFor([org("a"), org("b")], everywhere, PROVIDER)).toBe(
			"selected",
		);
		expect(presetFor([managed], everywhere, PROVIDER)).toBe("customers");
		expect(
			presetFor([platform, org(PROVIDER), managed], everywhere, PROVIDER),
		).toBe("all");
	});

	it("calls a placement matching no preset Custom", () => {
		expect(presetFor([org("a"), managed], everywhere, PROVIDER)).toBe(
			"custom",
		);
		expect(presetFor([platform], everywhere, PROVIDER)).toBe("custom");
		expect(
			presetFor(
				[managed, org(PROVIDER), platform, org("a")],
				everywhere,
				PROVIDER,
			),
		).toBe("custom");
	});

	it("calls a placement Custom when its preset isn't offered for the role", () => {
		expect(
			presetFor([managed, org(PROVIDER), platform], operator, PROVIDER),
		).toBe("custom");
		expect(presetFor([managed], orgOnly, PROVIDER)).toBe("custom");
	});

	it("treats no places yet as Selected organizations", () => {
		expect(presetFor([], orgOnly, PROVIDER)).toBe("selected");
	});
});

describe("placesForPreset", () => {
	it("places a role at each selected organization", () => {
		expect(
			placesForPreset("selected", everywhere, ["a", "b"], PROVIDER),
		).toEqual([org("a"), org("b")]);
	});

	it("leaves out the provider org when the role can't apply there", () => {
		expect(
			placesForPreset("selected", operator, [PROVIDER, "a"], PROVIDER),
		).toEqual([org("a")]);
	});

	it("places a role at all customer organizations", () => {
		expect(placesForPreset("customers", operator, ["a"], PROVIDER)).toEqual(
			[managed],
		);
	});

	it("places a role everywhere: customers, the provider org and Global", () => {
		expect(placesForPreset("all", everywhere, ["a"], PROVIDER)).toEqual([
			managed,
			org(PROVIDER),
			platform,
		]);
	});

	it("round-trips through presetFor", () => {
		for (const preset of ["selected", "customers", "all"] as const) {
			expect(
				presetFor(
					placesForPreset(preset, everywhere, ["a"], PROVIDER),
					everywhere,
					PROVIDER,
				),
			).toBe(preset);
		}
	});
});
