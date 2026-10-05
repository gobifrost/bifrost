import { Badge } from "@/components/ui/badge";
import type { Place } from "@/services/access";

import { PlaceLabel } from "./PlaceLabel";

/** One place a person's access reaches. */
export function ReachChip({ place }: { place: Place }) {
	return (
		<Badge
			variant="secondary"
			data-place={place.kind}
			className="h-auto min-h-5 whitespace-normal bg-[var(--bf-reach-soft)] text-[var(--bf-reach)]"
		>
			<PlaceLabel place={place} />
		</Badge>
	);
}
