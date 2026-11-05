import { LogOut, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { getErrorMessage } from "@/lib/api-error";

/** Persistent, compact warning that the browser is using a support session. */
export function ImpersonationBanner() {
	const { isImpersonating, user, endImpersonation } = useAuth();
	const [isEnding, setIsEnding] = useState(false);
	if (!isImpersonating) return null;

	return (
		<div
			role="status"
			aria-label="Impersonation session"
			className="flex min-h-10 shrink-0 items-center justify-between gap-3 border-b border-[color:color-mix(in_oklch,var(--bf-warning)_45%,transparent)] bg-[color:color-mix(in_oklch,var(--bf-warning)_14%,var(--background))] px-3 py-1.5 text-sm sm:px-4"
		>
			<p className="flex min-w-0 items-center gap-2 text-foreground">
				<ShieldAlert aria-hidden="true" className="size-4 shrink-0 text-[var(--bf-warning)]" />
				<span className="flex min-w-0 items-baseline gap-1.5 truncate">
					<span className="truncate">
						Impersonating {user?.name || user?.email || "user"}
					</span>
					{user?.name && user.email ? (
						<span className="hidden truncate text-xs text-muted-foreground sm:inline">
							{user.email}
						</span>
					) : null}
				</span>
			</p>
			<Button
				type="button"
				variant="outline"
				size="sm"
				disabled={isEnding}
				onClick={() => {
					setIsEnding(true);
					void endImpersonation().catch((error: unknown) => {
						setIsEnding(false);
						toast.error("Could not end impersonation", {
							description: getErrorMessage(
								error,
								"The support session could not be ended.",
							),
						});
					});
				}}
			>
				<LogOut aria-hidden="true" className="size-4" />
				Exit
			</Button>
		</div>
	);
}
