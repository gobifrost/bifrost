import { Badge } from "@/components/ui/badge";
import type { Place } from "@/services/access";

import { PlaceLabel } from "./PlaceLabel";

/** One place a person's access reaches, on one line. */
export function ReachChip({ place }: { place: Place }) {
	return (
		<Badge
			variant="secondary"
			data-place={place.kind}
			title={place.label}
			className="max-w-full bg-[var(--bf-reach-soft)] text-[var(--bf-reach)]"
		>
			<PlaceLabel place={place} truncate />
		</Badge>
	);
}
