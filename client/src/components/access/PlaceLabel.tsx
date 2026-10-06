import { Building, Building2, Globe, House } from "lucide-react";

import type { Place, PlaceKind } from "@/services/access";

const PLACE_ICONS = {
	home: House,
	organization: Building2,
	managed_organizations: Building,
	platform: Globe,
} satisfies Record<PlaceKind, typeof House>;

/**
 * Where access applies, with an icon for the kind of place. `truncate` keeps
 * it on one line (chips); otherwise a long name wraps (table and list headers).
 */
export function PlaceLabel({
	place,
	truncate = false,
}: {
	place: Place;
	truncate?: boolean;
}) {
	const Icon = PLACE_ICONS[place.kind];
	return (
		<span className="inline-flex min-w-0 items-center gap-1.5">
			<Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
			<span
				className={
					truncate ? "min-w-0 truncate" : "[overflow-wrap:anywhere]"
				}
			>
				{place.label}
			</span>
		</span>
	);
}
