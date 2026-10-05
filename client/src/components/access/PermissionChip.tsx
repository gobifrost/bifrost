import { Badge } from "@/components/ui/badge";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type {
	AccessGrant,
	AccessGrantSource,
	PermissionCatalogEntry,
	Place,
} from "@/services/access";

type CatalogScope = PermissionCatalogEntry["scope"];
type ChipVariant = CatalogScope | "privileged";
type ChipGrant = Omit<AccessGrant, "scope"> & { scope: CatalogScope };

const BRIDGE_EDGE =
	"relative pl-3 before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:bg-[image:var(--bf-bridge-vertical)]";

const VARIES_EDGE =
	"relative pl-3 before:absolute before:inset-y-1 before:left-0 before:border-l-2 before:border-dashed before:border-current";

const VARIES_NOTE =
	"Some operations in this area are platform-wide and apply only through a Global placement.";

function chipVariant(
	grant: ChipGrant,
	catalogEntry: PermissionCatalogEntry | undefined,
): ChipVariant {
	if (catalogEntry?.privileged.includes(grant.permission))
		return "privileged";
	return grant.scope;
}

function whereClause(place: Place | undefined): string {
	if (!place) return "";
	switch (place.kind) {
		case "home":
		case "organization":
			return ` at ${place.organization_name ?? place.label}`;
		case "managed_organizations":
			return " in all customer organizations";
		case "platform":
			return " globally";
	}
}

function sourceLine(source: AccessGrantSource, place: Place | undefined) {
	const role =
		source.via === "base"
			? `${source.role_name} (base role)`
			: source.role_name;
	return `${role}${whereClause(place)}`;
}

/**
 * One permission a person holds. Solid when it applies per organization,
 * edged with the bridge gradient when it is platform-wide, edged with a dashed
 * line when it varies by operation, and in the warning tone when it is
 * privileged. Hover or focus lists the roles that give it.
 */
export function PermissionChip({
	grant,
	catalogEntry,
	place,
}: {
	grant: ChipGrant;
	catalogEntry?: PermissionCatalogEntry;
	place?: Place;
}) {
	const variant = chipVariant(grant, catalogEntry);
	const platformWide = grant.scope === "platform_wide";
	const varies = grant.scope === "varies";
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<Badge
					asChild
					variant={variant === "privileged" ? "warning" : "secondary"}
				>
					<button
						type="button"
						data-variant={variant}
						className={cn(
							"h-auto min-h-5 cursor-default whitespace-normal",
							variant !== "privileged" &&
								"bg-[var(--bf-power-soft)] text-[var(--bf-power)]",
							platformWide && BRIDGE_EDGE,
							varies && VARIES_EDGE,
						)}
					>
						<span>{catalogEntry?.title ?? grant.domain}</span>
						<span className="font-normal">{grant.action}</span>
						{platformWide && (
							<span className="text-xs font-semibold">
								Platform-wide
							</span>
						)}
					</button>
				</Badge>
			</TooltipTrigger>
			<TooltipContent>
				<ul className="space-y-0.5">
					{grant.sources.map((source) => (
						<li key={`${source.role_id}:${source.via}`}>
							{sourceLine(source, place)}
						</li>
					))}
				</ul>
				{varies && <p className="mt-1">{VARIES_NOTE}</p>}
			</TooltipContent>
		</Tooltip>
	);
}
