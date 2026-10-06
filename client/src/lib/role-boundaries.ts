/** Plain-language wording and placement presets for where a role applies. */

export type BoundaryKind =
	"organization" | "managed_organizations" | "platform";

/** One place a role is put. */
export interface RolePlace {
	kind: BoundaryKind;
	organization_id: string | null;
}

/** What the server allows for a role: its boundary kinds and the provider org. */
export interface PresetRole {
	boundary_kinds: BoundaryKind[];
	provider_organization_allowed: boolean;
}

/**
 * "selected" = each chosen organization; "customers" = all customer
 * organizations; "all" = customers, the provider organization and Global.
 */
export type PlacementPreset = "selected" | "customers" | "all";

/** "In Contoso", "In all customer organizations", "Platform-wide". */
export function placeLabel(
	kind: BoundaryKind,
	organizationName: string,
): string {
	switch (kind) {
		case "organization":
			return `In ${organizationName}`;
		case "managed_organizations":
			return "In all customer organizations";
		case "platform":
			return "Platform-wide";
	}
}

const MANAGED: RolePlace = {
	kind: "managed_organizations",
	organization_id: null,
};
const GLOBAL: RolePlace = { kind: "platform", organization_id: null };

function placeKey(place: RolePlace): string {
	return `${place.kind}:${place.organization_id ?? ""}`;
}

/** The presets a role can use, in display order. */
export function offeredPresets(role: PresetRole): PlacementPreset[] {
	const kinds = new Set(role.boundary_kinds);
	const presets: PlacementPreset[] = [];
	if (kinds.has("organization")) presets.push("selected");
	if (kinds.has("managed_organizations")) presets.push("customers");
	if (
		kinds.has("organization") &&
		kinds.has("managed_organizations") &&
		kinds.has("platform") &&
		role.provider_organization_allowed
	)
		presets.push("all");
	return presets;
}

function matches(
	places: RolePlace[],
	preset: PlacementPreset,
	role: PresetRole,
	providerOrgId: string,
): boolean {
	if (preset === "selected")
		return places.every((place) => place.kind === "organization");
	const expected = placesForPreset(preset, role, [], providerOrgId).map(
		placeKey,
	);
	const actual = new Set(places.map(placeKey));
	return (
		actual.size === expected.length &&
		expected.every((key) => actual.has(key))
	);
}

/**
 * The offered preset these places match, or "custom" when none does. No places
 * yet counts as Selected organizations: the editor is waiting for a choice.
 */
export function presetFor(
	places: RolePlace[],
	role: PresetRole,
	providerOrgId: string,
): PlacementPreset | "custom" {
	return (
		offeredPresets(role).find((preset) =>
			matches(places, preset, role, providerOrgId),
		) ?? "custom"
	);
}

/** The places a preset stands for. */
export function placesForPreset(
	preset: PlacementPreset,
	role: PresetRole,
	selectedOrgIds: string[],
	providerOrgId: string,
): RolePlace[] {
	switch (preset) {
		case "selected":
			return selectedOrgIds
				.filter(
					(id) =>
						role.provider_organization_allowed ||
						id !== providerOrgId,
				)
				.map((id) => ({ kind: "organization", organization_id: id }));
		case "customers":
			return [MANAGED];
		case "all":
			return [
				MANAGED,
				{ kind: "organization", organization_id: providerOrgId },
				GLOBAL,
			];
	}
}
