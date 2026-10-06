import { Badge } from "@/components/ui/badge";
import { permissionDisplayName, WILDCARD } from "@/lib/permission-words";
import { cn } from "@/lib/utils";
import type { PermissionCatalogEntry } from "@/services/access";

import { BRIDGE_EDGE } from "./PermissionChip";

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
	const name = permissionDisplayName(permission, entry);
	if (permission === WILDCARD)
		return (
			<Badge
				variant="warning"
				data-variant="privileged"
				className={cn("h-auto min-h-6", BRIDGE_EDGE)}
			>
				{name}
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
			<span>{name}</span>
			{platformWide && (
				<span className="text-xs font-semibold">Platform-Wide</span>
			)}
		</Badge>
	);
}
