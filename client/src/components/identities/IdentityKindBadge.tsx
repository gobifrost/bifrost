import { Badge } from "@/components/ui/badge";
import { identityKindLabel, type IdentityKind } from "@/services/identities";

/**
 * Default, Global or Custom; Global in the reach colours, since it runs
 * the work of no organization.
 */
export function IdentityKindBadge({ kind }: { kind: IdentityKind }) {
	return (
		<Badge
			variant={kind === "custom" ? "outline" : "secondary"}
			data-kind={kind}
			className={
				kind === "global_default"
					? "bg-[var(--bf-reach-soft)] text-[var(--bf-reach)]"
					: undefined
			}
		>
			{identityKindLabel(kind)}
		</Badge>
	);
}
