import { IdentityGlyph } from "@/components/identities/IdentityGlyph";
import type { AuditLogEntry } from "@/hooks/useAuditLog";
import { storedTrace } from "@/lib/access-trace";

/** Who did it: their email or name, or the event's source when no one signed in. */
function actorName(entry: AuditLogEntry): string {
	return (
		entry.actor.user_email ||
		entry.actor.user_name ||
		(entry.source !== "http" ? `(${entry.source})` : "(unauthenticated)")
	);
}

/**
 * Who an audit event acted as. Every organization's default identity has
 * the same name, so an identity shows the hexagon glyph and its
 * organization ("Default Identity · Contoso"): its home by name where
 * `organizationName` knows it, else the organization the event carries.
 * Anyone else shows by email or name.
 */
export function RunUserLabel({
	entry,
	organizationName,
}: {
	entry: AuditLogEntry;
	organizationName: (organizationId: string) => string | undefined;
}) {
	const runUser = storedTrace(entry.details)?.steps.find(
		(step) => step.key === "run_user",
	);
	if (!runUser?.facts.identity_kind)
		return (
			<span className="[overflow-wrap:anywhere]">{actorName(entry)}</span>
		);

	const home = runUser.facts.home_organization_id;
	const organization =
		typeof home === "string"
			? organizationName(home) || entry.actor.organization_name
			: "Global";
	return (
		<span className="inline-flex min-w-0 items-center gap-2">
			<IdentityGlyph className="h-6 w-6 sm:h-6 sm:w-6 [&>svg:last-child]:size-3" />
			<span className="min-w-0 [overflow-wrap:anywhere]">
				{[entry.actor.user_name || "Identity", organization]
					.filter(Boolean)
					.join(" · ")}
			</span>
		</span>
	);
}
