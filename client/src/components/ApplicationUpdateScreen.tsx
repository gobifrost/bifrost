import { Loader2 } from "lucide-react";
import { type ReactNode, useState, useSyncExternalStore } from "react";

import {
	getApplicationUpdateInProgress,
	subscribeToApplicationUpdate,
} from "@/lib/application-update";

interface ApplicationUpdateScreenProps {
	fullScreen?: boolean;
}

const DEFAULT_LOGO_URL = "/logo.svg";

function getAppliedSquareLogoUrl(): string {
	const cssUrl = document.documentElement.style
		.getPropertyValue("--logo-square-url")
		.trim();
	const match = cssUrl.match(/^url\((['"]?)(.*)\1\)$/);
	return match?.[2] || DEFAULT_LOGO_URL;
}

export function ApplicationUpdateScreen({
	fullScreen = false,
}: ApplicationUpdateScreenProps) {
	const [logoUrl, setLogoUrl] = useState(getAppliedSquareLogoUrl);

	return (
		<div
			className={
				fullScreen
					? "flex h-screen w-screen items-center justify-center bg-background p-6"
					: "flex h-full min-h-[20rem] w-full items-center justify-center bg-background p-6"
			}
			role="status"
			aria-live="polite"
		>
			<div className="flex max-w-sm flex-col items-center text-center">
				<img
					src={logoUrl}
					alt="Application logo"
					className="mb-5 h-16 w-16 object-contain"
					onError={() => setLogoUrl(DEFAULT_LOGO_URL)}
				/>
				<h1 className="text-xl font-semibold tracking-tight">
					Application updated
				</h1>
				<div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
					<Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
					<span>Loading the latest version…</span>
				</div>
			</div>
		</div>
	);
}

export function ApplicationUpdateGate({ children }: { children: ReactNode }) {
	const updateInProgress = useSyncExternalStore(
		subscribeToApplicationUpdate,
		getApplicationUpdateInProgress,
		() => false,
	);

	return updateInProgress ? (
		<ApplicationUpdateScreen fullScreen />
	) : (
		children
	);
}
