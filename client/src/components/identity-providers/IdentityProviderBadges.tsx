import { CheckCircle2, CircleSlash, Clock, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatDateShort } from "@/lib/utils";
import {
	credentialStatus,
	type IdentityProvider,
} from "@/services/identity-providers";

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
