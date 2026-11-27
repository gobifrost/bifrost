/**
 * Account-menu prompt for setting up a passkey.
 */

import { useNavigate } from "react-router-dom";
import { Fingerprint } from "lucide-react";

import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { usePasskeyList } from "@/hooks/usePasskeys";
import { supportsPasskeys } from "@/services/passkeys";

const DISMISSED_KEY = "passkey_banner_dismissed";

function isDismissed(): boolean {
	try {
		return localStorage.getItem(DISMISSED_KEY) === "true";
	} catch {
		return false;
	}
}

export function PasskeySetupBadge() {
	const navigate = useNavigate();
	const isSupported = supportsPasskeys();
	const { data: passkeyData, isLoading } = usePasskeyList();

	if (
		isLoading ||
		!isSupported ||
		isDismissed() ||
		(passkeyData && passkeyData.count > 0)
	) {
		return null;
	}

	return (
		<DropdownMenuItem
			className="min-h-11"
			onSelect={() => navigate("/user-settings/security")}
		>
			<Fingerprint className="size-4" />
			Set Up Passkey
			<span
				aria-hidden="true"
				data-slot="passkey-setup-indicator"
				className="ml-auto size-2 rounded-full bg-primary"
			/>
		</DropdownMenuItem>
	);
}
