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
 * the same name, so an identity shows the hexagon glyph and its home
 * organization ("Default Identity · Contoso"), never the organization the
 * event targeted. Anyone else shows by email or name.
 */
export function RunUserLabel({ entry }: { entry: AuditLogEntry }) {
	const runUser = storedTrace(entry.details)?.steps.find(
		(step) => step.key === "run_user",
	);
	if (!runUser?.facts.identity_kind)
		return (
			<span className="[overflow-wrap:anywhere]">{actorName(entry)}</span>
		);

	const organization =
		typeof runUser.facts.home_organization_id === "string"
			? entry.actor.home_organization_name
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
