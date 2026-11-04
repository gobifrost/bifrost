import type { ReactNode } from "react";
import { ArrowLeft, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber, formatRelativeTime } from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentStats = components["schemas"]["AgentStatsResponse"];
export interface TuneHeaderProps {
	agentId: string | undefined;
	agentName: string | undefined;
	flaggedCount: number;
	stats: AgentStats | null;
	statsLoading: boolean;
	action?: ReactNode;
}

export function TuneHeader({
	agentId,
	agentName,
	flaggedCount,
	stats,
	statsLoading,
	action,
}: TuneHeaderProps) {
	return (
		<header className="shrink-0 space-y-2">
			<div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1">
				<div className="flex min-w-0 flex-wrap items-center gap-x-3">
					<Link
						to={agentId ? `/agents/${agentId}` : "/agents"}
						className="inline-flex min-h-11 min-w-0 items-center gap-2 text-sm text-muted-foreground [overflow-wrap:anywhere] hover:text-foreground"
					>
						<ArrowLeft className="size-4 shrink-0" />
						{agentName ?? "Back to Agent"}
					</Link>
					<Link
						to={agentId ? `/agents/${agentId}/review` : "/agents"}
						className="inline-flex min-h-11 items-center text-sm text-muted-foreground hover:text-foreground"
					>
						Review Flagged Runs
					</Link>
				</div>
				<div
					className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
					aria-label="Agent Statistics"
				>
					<span>{formatNumber(flaggedCount)} Flagged Runs</span>
					{statsLoading ? (
						<Skeleton
							data-testid="stat-skeleton"
							className="h-4 w-56"
						/>
					) : (
						stats && (
							<>
								<span>
									Runs (7d){" "}
									<strong className="font-medium text-foreground">
										{formatNumber(stats.runs_7d)}
									</strong>
								</span>
								<span>
									Success Rate{" "}
									<strong className="font-medium text-foreground">
										{Math.round(
											(stats.success_rate ?? 0) * 100,
										)}
										%
									</strong>
								</span>
								<span>
									Last Run{" "}
									{stats.last_run_at
										? formatRelativeTime(stats.last_run_at)
										: "—"}
								</span>
							</>
						)
					)}
				</div>
			</div>
			<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
				<h1 className="flex items-center gap-2 font-display text-2xl font-semibold tracking-tight">
					<Sparkles
						className="size-5 text-primary"
						aria-hidden="true"
					/>
					Tune Agent
				</h1>
				{action}
			</div>
		</header>
	);
}
