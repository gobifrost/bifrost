import { Building2, Globe } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { identityOrganization, type Identity } from "@/services/identities";

import { IdentityGlyph } from "./IdentityGlyph";

/**
 * The organization an identity belongs to, as a chip; null is Global, in the
 * reach colours like the global identity's row.
 */
export function IdentityOrganizationChip({
	organizationName,
}: {
	organizationName: string | null;
}) {
	const isGlobal = organizationName === null;
	const Icon = isGlobal ? Globe : Building2;
	return (
		<Badge
			variant={isGlobal ? "secondary" : "outline"}
			aria-label="Organization"
			data-global={isGlobal || undefined}
			className={cn(
				"max-w-full font-normal",
				isGlobal && "bg-[var(--bf-reach-soft)] text-[var(--bf-reach)]",
			)}
		>
			<Icon aria-hidden="true" />
			<span className="min-w-0 truncate">
				{identityOrganization({ organization_name: organizationName })}
			</span>
		</Badge>
	);
}

/**
 * An identity where it's shown rather than written: the hexagon glyph, its
 * name, and its organization chip, since every default identity is named
 * "Default Identity".
 */
export function IdentityName({
	identity,
}: {
	identity: Pick<Identity, "name" | "organization_name">;
}) {
	return (
		<span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-2">
			<IdentityGlyph className="h-6 w-6 sm:h-6 sm:w-6 [&>svg:last-child]:size-3" />
			<span className="min-w-0 text-sm font-medium [overflow-wrap:anywhere]">
				{identity.name}
			</span>
			<IdentityOrganizationChip
				organizationName={identity.organization_name}
			/>
		</span>
	);
}
