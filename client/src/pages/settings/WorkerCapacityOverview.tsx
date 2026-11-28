import { useEffect } from "react";
import { Boxes, Loader2, RefreshCw } from "lucide-react";

import { SettingsLoadError } from "@/components/shared/SettingsLoadError";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { formatTime } from "@/lib/utils";
import { webSocketService } from "@/services/websocket";
import { usePools, type WorkerFormCapacity } from "@/services/workers";

const FORMS: Array<{ form: WorkerFormCapacity["form"]; label: string }> = [
	{ form: "static", label: "Local" },
	{ form: "job", label: "Kubernetes" },
	{ form: "external", label: "External" },
];

function capacityForForm(
	forms: WorkerFormCapacity[],
	form: WorkerFormCapacity["form"],
): WorkerFormCapacity {
	return (
		forms.find((entry) => entry.form === form) ?? {
			form,
			active: 0,
			reserved: 0,
			starting: 0,
			ready: 0,
			draining: 0,
		}
	);
}

export function WorkerCapacityOverview() {
	const { data, isLoading, isError, refetch, dataUpdatedAt } = usePools({
		includeIncarnations: true,
	});
	const overview = data?.capacity_overview;

	useEffect(() => {
		const unsubscribe = webSocketService.onPoolMessage((message) => {
			if (message.type === "worker_online" || message.type === "worker_offline") {
				void refetch();
			}
		});
		return unsubscribe;
	}, [refetch]);

	return (
		<Card>
			<CardHeader>
				<div className="flex flex-wrap items-center gap-2">
					<Boxes aria-hidden="true" className="size-5" />
					<CardTitle>Capacity Overview</CardTitle>
					<Button
						type="button"
						variant="outline"
						size="icon"
						className="ml-auto min-h-11 min-w-11"
						disabled={isLoading}
						onClick={() => void refetch()}
						aria-label="Refresh Capacity Overview"
					>
						<RefreshCw aria-hidden="true" className="size-4" />
					</Button>
				</div>
				<CardDescription>
					Canonical managed-worker capacity from the latest worker query.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				{isLoading && !overview && (
					<p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
						<Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" />
						Loading capacity overview…
					</p>
				)}
				{isError && !overview && (
					<SettingsLoadError
						name="worker capacity overview"
						onRetry={() => void refetch()}
					/>
				)}
				{overview && (
					<>
						<div className="grid gap-4 sm:grid-cols-2">
							<div>
								<p className="text-sm text-muted-foreground">Managed Workers</p>
								<p className="mt-1 text-lg font-semibold tabular-nums">
									{overview.managed_in_use} / {overview.global_managed_limit}
								</p>
							</div>
							{overview.cloudflare_profile_limit != null && (
								<div>
									<p className="text-sm text-muted-foreground">Cloudflare Profile Bound</p>
									<p className="mt-1 text-lg font-semibold tabular-nums">
										{overview.cloudflare_profile_limit}
									</p>
								</div>
							)}
						</div>

						<div className="grid gap-3 border-t pt-5 sm:grid-cols-3">
							{FORMS.map(({ form, label }) => {
								const capacity = capacityForForm(overview.forms ?? [], form);
								return (
									<div key={form} className="space-y-1 text-sm">
										<p className="font-medium">{label}</p>
										<p className="text-muted-foreground tabular-nums">
											Ready {capacity.ready} · Starting {capacity.starting}
										</p>
									</div>
								);
							})}
						</div>

						{overview.cleanup_pending_count > 0 && (
							<p role="status" className="text-sm text-muted-foreground">
								{overview.cleanup_pending_count} resource{overview.cleanup_pending_count === 1 ? " is" : "s are"} pending cleanup; removal is not yet confirmed.
							</p>
						)}
					</>
				)}
				{!isLoading && !isError && !overview && (
					<p role="status" className="text-sm text-muted-foreground">
						Capacity overview is not available for this snapshot.
					</p>
				)}
				<p className="text-xs text-muted-foreground">
					{dataUpdatedAt > 0
						? `Snapshot refreshed ${formatTime(new Date(dataUpdatedAt))}.`
						: "Refresh to request a new capacity snapshot."}
				</p>
			</CardContent>
		</Card>
	);
}
