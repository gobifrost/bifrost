import { usePresentationStore } from "@/stores/presentationStore";
import { CheckCircle2, CircleSlash, Clock, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatDateShort } from "@/lib/utils";
import type { components } from "@/lib/v1";
import {
	credentialStatus,
	identitySourceLabel,
	type IdentityProvider,
} from "@/services/identity-providers";

/**
 * Where a user's identity comes from. While an activation, role grant or
 * rename from the identity provider waits for the batched access pass, says
 * so: Bifrost applies those within about two minutes, not at once.
 */
export function IdentitySourceLabel({
	user,
}: {
	user: Pick<components["schemas"]["UserPublic"], "identity_source">;
}) {
	const presentationMode = usePresentationStore((state) => state.enabled);
	const source = user.identity_source;
	return (
		<span className="inline-flex flex-wrap items-center gap-2">
			<span data-pii={!!source || undefined}>
				{identitySourceLabel(user)}
			</span>
			{source?.access_pending_since ? (
				<Badge
					variant="secondary"
					data-pii="off"
					title={
						presentationMode
							? "Changes apply within about two minutes"
							: `Changes from ${source.name} apply within about two minutes`
					}
				>
					<Clock aria-hidden="true" className="h-3.5 w-3.5" />
					Access Pending
				</Badge>
			) : null}
		</span>
	);
}

/**
 * An inactive user's status: Activating while the access pass is about to
 * turn on a new hire from an identity provider, Inactive otherwise.
 */
export function InactiveStatusBadge({
	user,
}: {
	user: Pick<components["schemas"]["UserPublic"], "identity_source">;
}) {
	const presentationMode = usePresentationStore((state) => state.enabled);
	const source = user.identity_source;
	return source?.activating ? (
		<Badge
			variant="info"
			title={
				presentationMode
					? "Bifrost activates this user within about two minutes"
					: `${source.name} added this user; Bifrost activates them within about two minutes`
			}
		>
			<Clock aria-hidden="true" className="h-3.5 w-3.5" />
			Activating
		</Badge>
	) : (
		<Badge variant="secondary">Inactive</Badge>
	);
}

/** Whether the connection accepts provisioning requests. */
export function ProvisioningStatusBadge({ enabled }: { enabled: boolean }) {
	return enabled ? (
		<Badge variant="success">
			<CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" />
			Enabled
		</Badge>
	) : (
		<Badge variant="secondary">
			<CircleSlash aria-hidden="true" className="h-3.5 w-3.5" />
			Disabled
		</Badge>
	);
}

/** The token's expiry, flagged once it is close or past. */
export function CredentialExpiryBadge({
	token,
}: {
	token: IdentityProvider["token"];
}) {
	const status = credentialStatus(token);
	if (!token || status === "none") {
		return <Badge variant="destructive">No Token</Badge>;
	}
	const date = formatDateShort(token.expires_at);
	if (status === "expired") {
		return (
			<Badge variant="destructive" title="This token has expired">
				<XCircle aria-hidden="true" className="h-3.5 w-3.5" />
				Expired {date}
			</Badge>
		);
	}
	if (status === "expiring") {
		return (
			<Badge variant="warning" title="This token expires soon">
				<Clock aria-hidden="true" className="h-3.5 w-3.5" />
				{date}
			</Badge>
		);
	}
	return <span className="text-sm tabular-nums">{date}</span>;
}
