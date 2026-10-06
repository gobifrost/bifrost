import { Badge } from "@/components/ui/badge";
import { permissionActionWord, permissionParts } from "@/lib/permission-words";
import { cn } from "@/lib/utils";
import type { PermissionCatalogEntry } from "@/services/access";

import { BRIDGE_EDGE } from "./PermissionChip";

/** Platform Admin's one permission: everything except reading secrets. */
const WILDCARD = "*";

/**
 * One permission a role grants, in the access map's power colours: the warning
 * tone when privileged, the bridge edge when platform-wide.
 */
export function GrantChip({
	permission,
	entry,
}: {
	permission: string;
	entry: PermissionCatalogEntry | undefined;
}) {
	if (permission === WILDCARD)
		return (
			<Badge
				variant="warning"
				data-variant="privileged"
				className={cn("h-auto min-h-6", BRIDGE_EDGE)}
			>
				Every permission
			</Badge>
		);
	const privileged = !!entry?.privileged.includes(permission);
	const platformWide = entry?.scope === "platform_wide";
	return (
		<Badge
			variant={privileged ? "warning" : "secondary"}
			data-variant={privileged ? "privileged" : entry?.scope}
			className={cn(
				"h-auto min-h-6 whitespace-normal",
				!privileged &&
					"bg-[var(--bf-power-soft)] text-[var(--bf-power)]",
				platformWide && BRIDGE_EDGE,
			)}
		>
			<span>{entry?.title ?? permissionParts(permission).domain}</span>
			<span className="font-normal">
				{permissionActionWord(permission)}
			</span>
			{platformWide && (
				<span className="text-xs font-semibold">Platform-wide</span>
			)}
		</Badge>
	);
}
