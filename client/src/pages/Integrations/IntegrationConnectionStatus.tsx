import {
	CheckCircle2,
	ChevronDown,
	CircleMinus,
	TriangleAlert,
	XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Popover,
	PopoverContent,
	PopoverDescription,
	PopoverHeader,
	PopoverTitle,
	PopoverTrigger,
} from "@/components/ui/popover";
import type { Integration } from "@/services/integrations";

type ConnectionHealth = "Connected" | "Degraded" | "Failed" | "None";

const healthStyles: Record<ConnectionHealth, string> = {
	Connected:
		"border-[var(--bf-success)]/30 bg-[var(--bf-success)]/10 text-[var(--bf-success)] hover:bg-[var(--bf-success)]/15",
	Degraded:
		"border-[var(--bf-warning)]/30 bg-[var(--bf-warning)]/10 text-[var(--bf-warning)] hover:bg-[var(--bf-warning)]/15",
	Failed: "border-destructive/30 bg-destructive/10 text-destructive hover:bg-destructive/15",
	None: "border-border/70 bg-muted/40 text-muted-foreground hover:bg-muted/70",
};

function connectionCounts(integration: Integration) {
	const statuses = integration.connection_status_counts ?? {};
	const connected = (statuses.completed ?? 0) + (statuses.connected ?? 0);
	const failed = statuses.failed ?? 0;
	const other = Object.entries(statuses).reduce(
		(total, [status, count]) =>
			status === "completed" ||
			status === "connected" ||
			status === "failed"
				? total
				: total + count,
		0,
	);
	return { connected, failed, other };
}

function connectionHealth(integration: Integration): ConnectionHealth {
	const { connected, failed } = connectionCounts(integration);
	if (connected > 0 && failed > 0) return "Degraded";
	if (connected > 0) return "Connected";
	if (failed > 0) return "Failed";
	return "None";
}

function StatusCount({
	label,
	count,
	tone,
}: {
	label: string;
	count: number;
	tone: "success" | "danger" | "muted";
}) {
	const Icon =
		tone === "success"
			? CheckCircle2
			: tone === "danger"
				? XCircle
				: CircleMinus;
	const color =
		tone === "success"
			? "text-[var(--bf-success)]"
			: tone === "danger"
				? "text-destructive"
				: "text-muted-foreground";
	return (
		<div className="flex items-center justify-between gap-6">
			<dt className="flex items-center gap-2 text-muted-foreground">
				<Icon aria-hidden="true" className={`size-4 ${color}`} />
				{label}
			</dt>
			<dd className="font-medium tabular-nums">{count}</dd>
		</div>
	);
}

export function IntegrationConnectionStatus({
	integration,
}: {
	integration: Integration;
}) {
	if (!integration.has_oauth_config)
		return <span className="text-muted-foreground">Not monitored</span>;

	const health = connectionHealth(integration);
	const counts = connectionCounts(integration);
	const HealthIcon =
		health === "Connected"
			? CheckCircle2
			: health === "Degraded"
				? TriangleAlert
				: health === "Failed"
					? XCircle
					: CircleMinus;

	return (
		<div
			className="relative z-10"
			onClick={(event) => event.stopPropagation()}
		>
			<Popover>
				<PopoverTrigger asChild>
					<Button
						type="button"
						variant="outline"
						size="xs"
						aria-label={`OAuth connection health: ${health}`}
						className={healthStyles[health]}
					>
						<HealthIcon
							aria-hidden="true"
							data-icon="inline-start"
						/>
						{health}
						<ChevronDown
							aria-hidden="true"
							data-icon="inline-end"
						/>
					</Button>
				</PopoverTrigger>
				<PopoverContent
					align="end"
					className="w-64 gap-3"
					aria-label="OAuth connection breakdown"
				>
					<PopoverHeader>
						<PopoverTitle>OAuth connections</PopoverTitle>
						<PopoverDescription>
							Default and organization overrides
						</PopoverDescription>
					</PopoverHeader>
					<dl className="grid gap-2.5 border-t border-border pt-3">
						<StatusCount
							label="Connected"
							count={counts.connected}
							tone="success"
						/>
						<StatusCount
							label="Failed"
							count={counts.failed}
							tone="danger"
						/>
						<StatusCount
							label="Other"
							count={counts.other}
							tone="muted"
						/>
					</dl>
				</PopoverContent>
			</Popover>
		</div>
	);
}
