import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { FlaggedRunCard } from "@/components/agents/FlaggedRunCard";
import { FleetReadError } from "./FleetReadError";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];
export function TuningFlaggedRuns({
	runs,
	total,
	loading,
	error,
	cached,
	fetching,
	onRetry,
	hasMore,
	loadingMore,
	moreError,
	onLoadMore,
}: {
	runs: AgentRun[];
	total: number;
	loading: boolean;
	error: boolean;
	cached: boolean;
	fetching: boolean;
	onRetry: () => void;
	hasMore: boolean;
	loadingMore: boolean;
	moreError: boolean;
	onLoadMore: () => void;
}) {
	return (
		<>
			{error ? (
				<FleetReadError
					resource="flagged runs"
					cached={cached}
					pending={fetching}
					onRetry={onRetry}
				/>
			) : null}
			<div className="space-y-3">
				{error && !cached ? null : loading ? (
					<Skeleton className="h-24 w-full" />
				) : !runs.length ? (
					<p className="rounded-[var(--bf-radius-surface)] border border-dashed p-4 text-center text-sm text-muted-foreground">
						No flagged runs. Mark a run thumbs-down from the runs
						tab to tune against it.
					</p>
				) : (
					<div className="flex flex-col gap-2">
						{runs.map((run) => (
							<FlaggedRunCard key={run.id} run={run} />
						))}
					</div>
				)}
				{hasMore ? (
					<div className="space-y-2">
						{moreError ? (
							<p
								role="alert"
								className="text-sm text-[var(--bf-danger)]"
							>
								Could not load more runs. Loaded runs are still
								available.
							</p>
						) : null}
						<Button
							type="button"
							variant="outline"
							disabled={fetching}
							onClick={onLoadMore}
						>
							{loadingMore
								? "Loading more runs…"
								: moreError
									? "Retry loading more runs"
									: "Load more flagged runs"}
						</Button>
						<p className="text-xs text-muted-foreground">
							{runs.length} of {total} loaded
						</p>
					</div>
				) : null}
			</div>
		</>
	);
}
