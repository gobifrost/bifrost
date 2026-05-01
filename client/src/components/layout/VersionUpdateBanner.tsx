import { Button } from "@/components/ui/button";
import { useVersionCheck } from "@/hooks/useVersionCheck";

export function VersionUpdateBanner() {
	const updateAvailable = useVersionCheck();
	if (!updateAvailable) return null;

	return (
		<div
			role="status"
			aria-live="polite"
			className="fixed top-0 left-0 right-0 z-50 flex items-center justify-center gap-3 bg-primary px-4 py-2 text-primary-foreground shadow-md"
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
