/** Plain-language wording for where a role applies. */

export type BoundaryKind =
	"organization" | "managed_organizations" | "platform";

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
