/**
 * Header affordance for setting up a passkey without occupying page content.
 */

import { useNavigate } from "react-router-dom";
import { Fingerprint } from "lucide-react";

import { Button } from "@/components/ui/button";
import { usePasskeyList } from "@/hooks/usePasskeys";
import { supportsPasskeys } from "@/services/passkeys";

const DISMISSED_KEY = "passkey_banner_dismissed";

export function PasskeySetupBadge() {
	const navigate = useNavigate();
	const isSupported = supportsPasskeys();
	const { data: passkeyData, isLoading } = usePasskeyList();

	if (
		isLoading ||
		!isSupported ||
		localStorage.getItem(DISMISSED_KEY) === "true" ||
		(passkeyData && passkeyData.count > 0)
	) {
		return null;
	}

	return (
		<Button
			variant="ghost"
			size="icon"
			className="relative mr-1 sm:mr-2"
			onClick={() => navigate("/user-settings/security")}
			title="Set up passkey"
			aria-label="Set up passkey"
		>
			<Fingerprint className="h-4 w-4" />
			<span
				aria-hidden="true"
				data-slot="passkey-setup-indicator"
				className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-primary ring-2 ring-background"
			/>
		</Button>
	);
}
