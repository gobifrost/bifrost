import {
	AlertTriangle,
	CheckCircle,
	Clock,
	GitBranch,
	Hash,
	Loader2,
	XCircle,
} from "lucide-react";
import type { ReactNode } from "react";

import {
	cn,
	formatDuration,
	formatNumber,
	formatRelativeTime,
} from "@/lib/utils";
import type { components } from "@/lib/v1";

import { SummaryPlaceholder } from "./SummaryPlaceholder";

type AgentRun = components["schemas"]["AgentRunResponse"];

export interface RunSummaryContentProps {
	run: AgentRun;
	highlight?: string;
	density?: "compact" | "comfortable";
	titleTrailing?: ReactNode;
	className?: string;
}

const STATUS_PRESENTATION = {
	completed: {
		label: "Completed",
		icon: CheckCircle,
		className: "bg-emerald-500/15 text-emerald-500",
	},
	failed: {
		label: "Failed",
		icon: XCircle,
		className: "bg-rose-500/15 text-rose-500",
	},
	running: {
		label: "Running",
		icon: Loader2,
		className: "bg-sky-500/15 text-sky-500",
	},
	budget_exceeded: {
		label: "Budget exceeded",
		icon: AlertTriangle,
		className: "bg-amber-500/15 text-amber-500",
	},
} as const;

function RunStatusIndicator({ status }: { status: string }) {
	const normalized = status.toLowerCase() as keyof typeof STATUS_PRESENTATION;
	const presentation = STATUS_PRESENTATION[normalized] ?? {
		label: status || "Unknown",
		icon: Clock,
		className: "bg-muted text-muted-foreground",
	};
	const Icon = presentation.icon;

	return (
		<span
			role="img"
			aria-label={`Status: ${presentation.label}`}
			className={cn(
				"mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full",
				presentation.className,
			)}
		>
			<Icon
				aria-hidden="true"
				className={cn(
					"h-3.5 w-3.5",
					normalized === "running" && "animate-spin",
				)}
			/>
		</span>
	);
}

export function RunSummaryContent({
	run,
	highlight,
	density = "comfortable",
	titleTrailing,
	className,
}: RunSummaryContentProps) {
	const query = highlight?.trim().toLowerCase() ?? "";
	const metadataEntries = Object.entries(run.metadata ?? {});
	const rankedMetadata = query
		? [...metadataEntries].sort((a, b) => {
				const aHit =
					a[0].toLowerCase().includes(query) ||
					a[1].toLowerCase().includes(query);
				const bHit =
					b[0].toLowerCase().includes(query) ||
					b[1].toLowerCase().includes(query);
				return Number(bHit) - Number(aHit);
			})
		: metadataEntries;
	const visibleChips = rankedMetadata.slice(0, 3);
	const overflow = metadataEntries.length - visibleChips.length;
	const startedAt = run.started_at ?? run.created_at;
	const bodyText = run.did ?? (run.error ? `Error: ${run.error}` : null);

	return (
		<div className={cn("flex min-w-0 flex-1 items-start gap-3", className)}>
			<RunStatusIndicator status={run.status} />
			<div
				className={cn(
					"flex min-w-0 flex-1 flex-col",
					density === "compact" ? "gap-0.5" : "gap-1.5",
				)}
			>
				<div className="flex min-w-0 items-center gap-2">
					<div
						className={cn(
							"min-w-0 flex-1 truncate",
							density === "compact" ? "text-[13px]" : "text-sm",
						)}
						title={run.asked ?? undefined}
					>
						{run.asked || (
							<SummaryPlaceholder
								status={run.summary_status}
								runStatus={run.status}
							/>
						)}
					</div>
					{run.parent_run_id ? (
						<span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-violet-700 ring-1 ring-violet-500/20 dark:text-violet-300">
							<GitBranch aria-hidden="true" className="h-2.5 w-2.5" />
							Delegated
						</span>
					) : null}
					{titleTrailing}
				</div>
				<div
					className={cn(
						"min-w-0 truncate text-muted-foreground",
						density === "compact" ? "text-[12px]" : "text-sm",
					)}
					title={bodyText ?? undefined}
				>
					{bodyText || (
						<SummaryPlaceholder
							status={run.summary_status}
							runStatus={run.status}
							muted
						/>
					)}
				</div>
				<div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
					<span className="inline-flex items-center gap-1">
						<Clock
							aria-hidden="true"
							className="h-[11px] w-[11px]"
						/>
						{formatRelativeTime(startedAt)}
					</span>
					{run.duration_ms != null ? (
						<>
							<span aria-hidden="true">·</span>
							<span>{formatDuration(run.duration_ms)}</span>
						</>
					) : null}
					<span aria-hidden="true">·</span>
					<span className="inline-flex items-center gap-1">
						<Hash
							aria-hidden="true"
							className="h-[11px] w-[11px]"
						/>
						{formatNumber(run.tokens_used)}
						<span className="sr-only">tokens</span>
					</span>
					{visibleChips.length > 0 ? (
						<span aria-hidden="true">·</span>
					) : null}
					<div className="inline-flex min-w-0 flex-wrap gap-1">
						{visibleChips.map(([key, value]) => {
							const isHit =
								query.length > 0 &&
								(key.toLowerCase().includes(query) ||
									value.toLowerCase().includes(query));
							return (
								<span
									key={key}
									title={`${key}=${value}`}
									className={cn(
										"inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]",
										isHit
											? "border-transparent bg-yellow-500/15 text-yellow-700 dark:text-yellow-300"
											: "border-border bg-card text-foreground",
									)}
								>
									<span className="text-muted-foreground">
										{key}
									</span>
									<span className="font-mono">{value}</span>
								</span>
							);
						})}
						{overflow > 0 ? (
							<span className="inline-flex items-center rounded border border-border bg-card px-1.5 py-0.5 text-[11px]">
								+{overflow}
							</span>
						) : null}
					</div>
				</div>
			</div>
		</div>
	);
}
