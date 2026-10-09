import type { MouseEvent } from "react";
import { Link } from "react-router-dom";
import { formatDistanceToNow } from "date-fns";
import {
	Building2,
	Calendar,
	Globe,
	TriangleAlert,
	Webhook,
	Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { EventSourceActions } from "@/components/events/EventSourceActions";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { MicrosoftGraphIcon } from "@/components/events/MicrosoftGraphIcon";
import {
	getGraphSourceSummary,
	isMicrosoftGraphSource,
} from "@/lib/graph-source";
import type { EventSource } from "@/services/events";

function getSourceTypeIcon(source: EventSource) {
	if (isMicrosoftGraphSource(source)) {
		return <MicrosoftGraphIcon className="h-4 w-4 text-[#1686d9]" />;
	}
	switch (source.source_type) {
		case "webhook":
			return <Webhook className="h-4 w-4" />;
		case "schedule":
			return <Calendar className="h-4 w-4" />;
		case "topic":
			return <Zap className="h-4 w-4" />;
	}
}

function getSourceTypeLabel(source: EventSource) {
	if (isMicrosoftGraphSource(source)) return "Microsoft Graph";
	switch (source.source_type) {
		case "webhook":
			return "Webhook";
		case "schedule":
			return "Schedule";
		case "topic":
			return "Topic";
	}
}

export function EventSourceCard({
	source,
	isPlatformAdmin,
	onToggleActive,
	onEdit,
	onDelete,
	updatePending,
}: {
	source: EventSource;
	isPlatformAdmin: boolean;
	onToggleActive: (source: EventSource, e: MouseEvent) => void;
	onEdit: (source: EventSource, e: MouseEvent) => void;
	onDelete: (source: EventSource, e: MouseEvent) => void;
	updatePending: boolean;
}) {
	const graphSummary = getGraphSourceSummary(source);
	const rateLimitedCount = source.webhook?.rate_limited_count_24h ?? 0;

	return (
		<Card className="overflow-hidden rounded-[var(--bf-radius-surface)]">
			<CardContent className="space-y-4 p-4">
				<div className="space-y-1">
					<div className="flex items-start justify-between gap-3">
						<div className="min-w-0 flex-1">
							<Link
								to={`/event-sources/${source.id}`}
								className="inline-flex min-h-11 items-center rounded-[var(--bf-radius-control)] text-left text-base font-medium leading-6 [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							>
								{source.name}
							</Link>
						</div>
						{isPlatformAdmin && (
							<EventSourceActions
								source={source}
								onEdit={onEdit}
								onDelete={onDelete}
							/>
						)}
					</div>
					<div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
						{source.organization_id ? (
							<span className="inline-flex items-center gap-1">
								<Building2 className="h-3 w-3" />
								{source.organization_name || "Organization"}
							</span>
						) : (
							<span className="inline-flex items-center gap-1">
								<Globe className="h-3 w-3" />
								Global
							</span>
						)}
						<span>·</span>
						<span className="whitespace-nowrap">
							{formatDistanceToNow(new Date(source.created_at), {
								addSuffix: true,
							})}
						</span>
						<span>·</span>
						<span className="inline-flex items-center gap-1">
							{getSourceTypeIcon(source)}
							{getSourceTypeLabel(source)}
						</span>
					</div>
				</div>

				{graphSummary && (
					<div className="space-y-1 text-sm text-muted-foreground">
						<div className="flex flex-wrap items-center gap-1.5">
							<span className="font-medium text-foreground">
								{graphSummary.userLabel}
							</span>
							<span>·</span>
							<span>{graphSummary.resourceLabel}</span>
							<span>·</span>
							<span>{graphSummary.changeLabel}</span>
						</div>
						{graphSummary.health !== "connected" && (
							<div className="inline-flex items-center gap-1.5 text-[var(--bf-warning)]">
								<TriangleAlert className="h-3 w-3" />
								Needs attention
							</div>
						)}
					</div>
				)}

				<div className="flex flex-wrap gap-2">
					{source.source_type === "topic" && source.event_type && (
						<Badge
							variant="outline"
							className="max-w-full whitespace-normal [overflow-wrap:anywhere] font-mono"
						>
							{source.event_type}
						</Badge>
					)}
					{source.webhook?.adapter_name && (
						<Badge
							variant="outline"
							className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
						>
							{isMicrosoftGraphSource(source)
								? "Microsoft Graph"
								: source.webhook.adapter_name}
						</Badge>
					)}
					<Badge
						variant="outline"
						className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
					>
						{source.subscription_count || 0} subscription
						{(source.subscription_count || 0) !== 1 ? "s" : ""}
					</Badge>
					<Badge
						variant="outline"
						className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
					>
						{source.event_count_24h || 0} event
						{(source.event_count_24h || 0) !== 1 ? "s" : ""} (24h)
					</Badge>
					{rateLimitedCount > 0 && (
						<Badge
							variant="destructive"
							className="max-w-full whitespace-normal [overflow-wrap:anywhere] text-xs"
							title="Webhooks rejected by per-source rate limit in the last 24h"
						>
							{rateLimitedCount} rate limited
						</Badge>
					)}
					{!isPlatformAdmin && (
						<Badge
							variant="outline"
							className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
						>
							{source.is_active ? "Active" : "Inactive"}
						</Badge>
					)}
				</div>

				<div className="flex flex-wrap items-center justify-between gap-3">
					{isPlatformAdmin ? (
						<div className="flex items-center gap-2">
							<Switch
								checked={source.is_active}
								onCheckedChange={() => {}}
								onClick={(e) => onToggleActive(source, e)}
								disabled={updatePending}
								aria-label={`Toggle ${source.name} active state`}
							/>
							<span className="text-sm text-muted-foreground">
								{source.is_active ? "Active" : "Inactive"}
							</span>
						</div>
					) : null}
				</div>
			</CardContent>
		</Card>
	);
}
