import { Button } from "@/components/ui/button";

export function ExecutionReadError({
	cached = false,
	pending = false,
	onRetry,
	onBack,
	backLabel = "Back to history",
}: {
	cached?: boolean;
	pending?: boolean;
	onRetry: () => void;
	onBack?: (() => void) | undefined;
	backLabel?: string;
}) {
	return (
		<div
			role="alert"
			className="min-w-0 space-y-3 rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-4"
		>
			<div className="space-y-1">
				<p className="font-medium">
					{cached
						? "Could not refresh execution"
						: "Could not load execution"}
				</p>
				<p className="text-sm text-muted-foreground">
					{cached
						? "Showing the last received details. Retry to check for updates."
						: "Try again. If the execution remains unavailable, it may have been removed or you may not have access."}
				</p>
			</div>
			<div className="flex flex-wrap gap-2">
				<Button
					variant="outline"
					className="min-h-11"
					disabled={pending}
					onClick={onRetry}
				>
					{pending ? "Retrying…" : "Retry execution"}
				</Button>
				{onBack && (
					<Button
						variant="ghost"
						className="min-h-11"
						onClick={onBack}
					>
						{backLabel}
					</Button>
				)}
			</div>
		</div>
	);
}
