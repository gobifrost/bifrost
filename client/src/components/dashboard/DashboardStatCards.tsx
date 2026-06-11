import { Link } from "react-router-dom";
import { Clock, DollarSign, TrendingUp, Zap } from "lucide-react";
import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { OutcomeSummary } from "@/lib/execution-buckets";

export interface InventoryCounts {
	workflows: number;
	forms: number;
	agents: number;
	apps: number;
}

export interface RoiSnapshot {
	/** Minutes saved in the last 24h. */
	timeSavedMinutes: number;
	value: number;
	valueUnit: string;
}

interface DashboardStatCardsProps {
	/** Human window label shared with the chart, e.g. "Last 7 days". */
	windowLabel: string;
	outcomes: OutcomeSummary;
	/** True when the window fetch hit the API row cap (counts are partial). */
	truncated: boolean;
	executionsLoading: boolean;
	executionsError: boolean;
	inventory: InventoryCounts;
	inventoryLoading: boolean;
	roi: RoiSnapshot | undefined;
	roiLoading: boolean;
}

/** Format minutes saved as a human-readable duration. */
function formatTimeSaved(minutes: number): string {
	const hours = Math.floor(minutes / 60);
	const mins = minutes % 60;
	if (hours > 0) {
		return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
	}
	return `${mins}m`;
}

function formatValue(value: number): string {
	return value.toLocaleString("en-US", {
		minimumFractionDigits: 0,
		maximumFractionDigits: 2,
	});
}

/** Rhea soft-tint accents — muted dots from the token palette, one per entity. */
const INVENTORY_ITEMS: Array<{
	key: keyof InventoryCounts;
	label: string;
	to: string;
	dotClass: string;
}> = [
	{
		key: "workflows",
		label: "Workflows",
		to: "/workflows",
		dotClass: "bg-teal-500/70",
	},
	{ key: "forms", label: "Forms", to: "/forms", dotClass: "bg-sky-500/70" },
	{
		key: "agents",
		label: "Agents",
		to: "/agents",
		dotClass: "bg-violet-500/70",
	},
	{ key: "apps", label: "Apps", to: "/apps", dotClass: "bg-amber-500/70" },
];

export function DashboardStatCards({
	windowLabel,
	outcomes,
	truncated,
	executionsLoading,
	executionsError,
	inventory,
	inventoryLoading,
	roi,
	roiLoading,
}: DashboardStatCardsProps) {
	const executionsUnavailable = executionsError || executionsLoading;
	// Honest about scope when the fetch hit the API row cap.
	const executionsWindowLabel = truncated
		? `${windowLabel} · latest 1,000 runs`
		: windowLabel;

	return (
		<div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
			{/* Success Rate — same window as the chart */}
			<Link to="/history" className="block">
				<Card className="h-full cursor-pointer transition-colors hover:border-primary/50">
					<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
						<CardTitle className="text-sm font-medium">
							Success Rate
						</CardTitle>
						<TrendingUp className="h-4 w-4 text-muted-foreground" />
					</CardHeader>
					<CardContent>
						{executionsUnavailable ? (
							executionsLoading ? (
								<Skeleton className="h-8 w-16" />
							) : (
								<div className="text-2xl font-bold text-muted-foreground">
									—
								</div>
							)
						) : (
							<div className="text-2xl font-bold">
								{outcomes.successRate === null
									? "—"
									: `${outcomes.successRate.toFixed(1)}%`}
							</div>
						)}
						<p className="text-xs text-muted-foreground">
							{executionsWindowLabel}
						</p>
					</CardContent>
				</Card>
			</Link>

			{/* Executions — count over the same window */}
			<Link to="/history" className="block">
				<Card className="h-full cursor-pointer transition-colors hover:border-primary/50">
					<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
						<CardTitle className="text-sm font-medium">
							Executions
						</CardTitle>
						<Zap className="h-4 w-4 text-muted-foreground" />
					</CardHeader>
					<CardContent>
						{executionsUnavailable ? (
							executionsLoading ? (
								<Skeleton className="h-8 w-16" />
							) : (
								<div className="text-2xl font-bold text-muted-foreground">
									—
								</div>
							)
						) : (
							<div className="text-2xl font-bold">
								{outcomes.total.toLocaleString()}
							</div>
						)}
						<p className="text-xs text-muted-foreground">
							{executionsWindowLabel}
						</p>
					</CardContent>
				</Card>
			</Link>

			{/* Inventory — what's built on the platform */}
			<Card className="h-full" data-testid="inventory-card">
				<CardHeader className="pb-2">
					<CardTitle className="text-sm font-medium">
						Inventory
					</CardTitle>
				</CardHeader>
				<CardContent>
					<div className="grid grid-cols-2 gap-x-3 gap-y-1">
						{INVENTORY_ITEMS.map((item) => (
							<Link
								key={item.key}
								to={item.to}
								className="-mx-1.5 flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-muted/50"
							>
								<span
									className={`h-1.5 w-1.5 shrink-0 rounded-full ${item.dotClass}`}
									aria-hidden
								/>
								<span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
									{item.label}
								</span>
								{inventoryLoading ? (
									<Skeleton className="h-4 w-6" />
								) : (
									<span className="text-sm font-semibold tabular-nums">
										{inventory[item.key].toLocaleString()}
									</span>
								)}
							</Link>
						))}
					</div>
				</CardContent>
			</Card>

			{/* Value — time saved + value generated, last 24h */}
			<Link to="/reports/roi" className="block">
				<Card className="h-full cursor-pointer transition-colors hover:border-primary/50">
					<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
						<CardTitle className="text-sm font-medium">
							Value (24h)
						</CardTitle>
						<DollarSign className="h-4 w-4 text-muted-foreground" />
					</CardHeader>
					<CardContent>
						{roiLoading ? (
							<Skeleton className="h-8 w-24" />
						) : (
							<div className="flex items-baseline gap-4">
								<div>
									<div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">
										{formatTimeSaved(
											roi?.timeSavedMinutes ?? 0,
										)}
									</div>
									<p className="flex items-center gap-1 text-xs text-muted-foreground">
										<Clock className="h-3 w-3" aria-hidden />
										saved
									</p>
								</div>
								<div>
									<div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">
										{formatValue(roi?.value ?? 0)}
									</div>
									<p className="text-xs text-muted-foreground">
										{roi?.valueUnit ?? "USD"}
									</p>
								</div>
							</div>
						)}
					</CardContent>
				</Card>
			</Link>
		</div>
	);
}
