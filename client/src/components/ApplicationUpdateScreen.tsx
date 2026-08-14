import { RefreshCw } from "lucide-react";
import { type ReactNode, useSyncExternalStore } from "react";

import {
	getApplicationUpdateInProgress,
	subscribeToApplicationUpdate,
} from "@/lib/application-update";

interface ApplicationUpdateScreenProps {
	fullScreen?: boolean;
}

export function ApplicationUpdateScreen({
	fullScreen = false,
}: ApplicationUpdateScreenProps) {
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
				<div className="mb-5 rounded-2xl bg-primary/10 p-4 text-primary ring-1 ring-primary/15">
					<RefreshCw className="h-7 w-7 animate-spin" aria-hidden="true" />
				</div>
				<h1 className="text-xl font-semibold tracking-tight">
					Application updated
				</h1>
				<p className="mt-2 text-sm text-muted-foreground">
					Loading the latest version…
				</p>
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
