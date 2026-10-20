import { Loader2, UserRoundCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { getErrorMessage } from "@/lib/api-error";
import { orgTarget } from "@/lib/authorization";
import type { components } from "@/lib/v1";
import { useAuthorization } from "@/services/authorization";

type User = components["schemas"]["UserPublic"];

/** Starts a browser-wide support session when the current policy permits it. */
export function ImpersonateUserButton({ user }: { user: User }) {
	const [isStarting, setIsStarting] = useState(false);
	const { user: currentUser, isImpersonating, startImpersonation } = useAuth();
	const authorization = useAuthorization();
	const target = orgTarget(user.organization_id);
	const canImpersonate =
		user.is_active &&
		!user.is_system &&
		!user.identity_kind &&
		user.id !== currentUser?.id &&
		!isImpersonating &&
		authorization.canAt("users.impersonate", target) &&
		(!user.is_protected ||
			authorization.canAt("privilegedaccess.readwrite", target));

	if (!canImpersonate) return null;

	return (
		<Button
			type="button"
			variant="outline"
			className="min-h-11 sm:min-h-9"
			disabled={isStarting}
			onClick={() => {
				setIsStarting(true);
				void startImpersonation({
					id: user.id,
					name: user.name || user.email,
					email: user.email,
				}).catch((error: unknown) => {
					setIsStarting(false);
					toast.error("Could not start impersonation", {
						description: getErrorMessage(
							error,
							"The support session could not be started.",
						),
					});
				});
			}}
		>
			{isStarting ? (
				<Loader2 aria-hidden="true" className="size-4 animate-spin" />
			) : (
				<UserRoundCheck aria-hidden="true" className="size-4" />
			)}
			Impersonate
		</Button>
	);
}
