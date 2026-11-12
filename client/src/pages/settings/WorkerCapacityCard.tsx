import { AlertTriangle, Boxes, Loader2 } from "lucide-react";

import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { SettingsLoadError } from "@/components/shared/SettingsLoadError";
import type { WorkerCapacityStatus } from "@/services/kubernetes";

function formatLifetime(seconds: number): string {
	if (seconds >= 3600 && seconds % 3600 === 0) {
		return `${seconds / 3600} hours`;
	}
	if (seconds >= 60 && seconds % 60 === 0) {
		return `${seconds / 60} minutes`;
	}
	return `${seconds} seconds`;
}

function timeAgo(iso: string): string {
	const seconds = Math.max(
		0,
		Math.floor((Date.now() - new Date(iso).getTime()) / 1000),
	);
	if (seconds < 60) return "just now";
	if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
	return `${Math.floor(seconds / 3600)}h ago`;
}

function WorkerCounts({ workers }: { workers: WorkerCapacityStatus["workers"] }) {
	const counts: {
		ready?: number;
		starting?: number;
		reserved?: number;
		draining?: number;
		lost?: number;
		retired?: number;
	} = workers ?? {};
	const entries = (
		[
			["Ready", counts.ready],
			["Starting", counts.starting],
			["Reserved", counts.reserved],
			["Draining", counts.draining],
			["Lost", counts.lost],
			["Retired", counts.retired],
		] as [string, number | undefined][]
	).filter((entry): entry is [string, number] => (entry[1] ?? 0) > 0);
	if (entries.length === 0) {
		return (
			<p className="text-sm text-muted-foreground">
				No external workers right now.
			</p>
		);
	}
	return (
		<dl className="flex flex-wrap gap-x-6 gap-y-1">
			{entries.map(([label, count]) => (
				<div key={label} className="flex items-baseline gap-2">
					<dt className="text-sm text-muted-foreground">{label}</dt>
					<dd className="text-sm font-semibold tabular-nums">{count}</dd>
				</div>
			))}
		</dl>
	);
}

/**
 * External capacity at a glance: provider bounds, live worker states, and
 * whether the saved scaling policy fits. A fetch failure stays inside this
 * card; the rest of the page keeps working.
 */
export function WorkerCapacityCard({
	capacity,
	loading,
	loadFailed,
	onRetry,
}: {
	capacity: WorkerCapacityStatus | null;
	loading: boolean;
	loadFailed: boolean;
	onRetry: () => void;
}) {
	return (
		<Card>
			<CardHeader>
				<div className="flex items-center gap-2">
					<Boxes aria-hidden="true" className="h-5 w-5" />
					<CardTitle>External Capacity</CardTitle>
				</div>
				<CardDescription>
					Cloudflare Containers bursting as platform workers.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				{loading && (
					<p
						role="status"
						className="flex items-center gap-2 text-sm text-muted-foreground"
					>
						<Loader2
							aria-hidden="true"
							className="size-4 animate-spin motion-reduce:animate-none"
						/>
						Loading…
					</p>
				)}
				{!loading && loadFailed && (
					<SettingsLoadError
						name="external capacity"
						onRetry={onRetry}
					/>
				)}
				{!loading && !loadFailed && capacity && !capacity.configured && (
					<p className="text-sm text-muted-foreground">
						No external capacity provider is configured. Workers
						scale on Kubernetes.
					</p>
				)}
				{!loading &&
					!loadFailed &&
					capacity?.configured &&
					capacity.error && (
						<div
							role="alert"
							className="flex min-w-0 gap-3 rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-4 text-sm"
						>
							<AlertTriangle
								aria-hidden="true"
								className="mt-0.5 size-4 shrink-0 text-[var(--bf-warning)]"
							/>
							<div className="min-w-0 space-y-2">
								<p>{capacity.error}</p>
								<Button
									type="button"
									variant="outline"
									size="sm"
									onClick={onRetry}
								>
									Retry
								</Button>
							</div>
						</div>
					)}
				{!loading &&
					!loadFailed &&
					capacity?.configured &&
					capacity.bounds && (
						<dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
							<div className="flex items-baseline justify-between gap-2">
								<dt className="text-muted-foreground">Scope</dt>
								<dd className="font-mono text-xs [overflow-wrap:anywhere]">
									{capacity.bounds.scope}
								</dd>
							</div>
							<div className="flex items-baseline justify-between gap-2">
								<dt className="text-muted-foreground">Build</dt>
								<dd className="font-mono text-xs [overflow-wrap:anywhere]">
									{capacity.bounds.worker_version}
								</dd>
							</div>
							<div className="flex items-baseline justify-between gap-2">
								<dt className="text-muted-foreground">
									Worker Memory
								</dt>
								<dd className="font-semibold tabular-nums">
									{capacity.bounds.memory_limit_bytes /
										(1024 * 1024)}{" "}
									MiB
								</dd>
							</div>
							<div className="flex items-baseline justify-between gap-2">
								<dt className="text-muted-foreground">
									Max Workers
								</dt>
								<dd className="font-semibold tabular-nums">
									{capacity.bounds.max_workers}
								</dd>
							</div>
							<div className="flex items-baseline justify-between gap-2">
								<dt className="text-muted-foreground">
									Max Lifetime
								</dt>
								<dd className="font-semibold tabular-nums">
									{formatLifetime(
										capacity.bounds.max_lifetime_seconds,
									)}
								</dd>
							</div>
						</dl>
					)}
				{!loading &&
					!loadFailed &&
					capacity?.configured &&
					(capacity.policy_mismatches ?? []).length > 0 && (
						<div role="alert" className="space-y-1">
							<p className="text-sm font-medium">
								Scaling policy does not fit this provider:
							</p>
							<ul className="list-disc space-y-1 pl-5 text-sm text-destructive [overflow-wrap:anywhere]">
								{capacity.policy_mismatches?.map((mismatch) => (
									<li key={mismatch}>{mismatch}</li>
								))}
							</ul>
						</div>
					)}
				{!loading && !loadFailed && capacity?.configured && (
					<WorkerCounts workers={capacity.workers} />
				)}
				{!loading &&
					!loadFailed &&
					capacity?.configured &&
					capacity.last_pass?.finished_at && (
						<p role="status" className="text-sm text-muted-foreground">
							{capacity.last_pass.ok
								? `Last reconcile ${timeAgo(capacity.last_pass.finished_at)} — healthy.`
								: `Last reconcile failed ${timeAgo(capacity.last_pass.finished_at)}: ${capacity.last_pass.error ?? "unknown error"}`}
						</p>
					)}
				{!loading &&
					!loadFailed &&
					capacity?.configured &&
					(capacity.recent_losses ?? []).length > 0 && (
						<div className="text-sm">
							<p className="text-muted-foreground">
								Recent fencing (last 24 hours):
							</p>
							<ul className="list-disc space-y-1 pl-5 tabular-nums">
								{capacity.recent_losses?.map((loss) => (
									<li key={loss.reason}>
										{loss.reason} × {loss.count}
									</li>
								))}
							</ul>
						</div>
					)}
			</CardContent>
		</Card>
	);
}
