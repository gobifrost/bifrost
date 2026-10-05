import { Building, Building2, Globe, House } from "lucide-react";

import type { Place, PlaceKind } from "@/services/access";

const PLACE_ICONS = {
	home: House,
	organization: Building2,
	managed_organizations: Building,
	platform: Globe,
} satisfies Record<PlaceKind, typeof House>;

/** Where access applies, with an icon for the kind of place. */
export function PlaceLabel({ place }: { place: Place }) {
	const Icon = PLACE_ICONS[place.kind];
	return (
		<span className="inline-flex items-center gap-1.5">
			<Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
			<span className="[overflow-wrap:anywhere]">{place.label}</span>
		</span>
	);
}
