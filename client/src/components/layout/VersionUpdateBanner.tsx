import { Button } from "@/components/ui/button";
import { useVersionCheck } from "@/hooks/useVersionCheck";

export function VersionUpdateBanner() {
	const updateAvailable = useVersionCheck();
	if (!updateAvailable) return null;

	return (
		<div
			role="status"
			aria-live="polite"
			className="flex w-full items-center justify-center gap-3 bg-primary px-4 py-2 text-primary-foreground shadow-md"
		>
			<span className="text-sm font-medium">
				A new version of Bifrost is available.
			</span>
			<Button
				size="sm"
				variant="secondary"
				onClick={() => window.location.reload()}
			>
				Refresh
			</Button>
		</div>
	);
}
