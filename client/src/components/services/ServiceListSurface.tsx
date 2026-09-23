import { Link, useNavigate } from "react-router-dom";
import {
	CircleStop,
	OctagonX,
	Play,
	Radio,
	RotateCcw,
	SlidersHorizontal,
	AlertTriangle,
} from "lucide-react";

import { ResourceCatalogCard } from "@/components/catalog/ResourceCatalogCard";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import type {
	ServiceAction,
	ServiceListItem,
} from "@/services/services";

export interface ServiceListSurfaceProps {
	services: ServiceListItem[];
	viewMode: "grid" | "table";
	isLoading?: boolean;
	isPlatformAdmin: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	onSelect?: (service: ServiceListItem) => void;
	/** Deep link to the service detail route (enables row/card links). */
	getDetailHref?: (service: ServiceListItem) => string;
	onAction?: (service: ServiceListItem, action: ServiceAction) => void;
	emptySearchActive?: boolean;
}

export const RESTART_WORDS: Record<string, string> = {
	always: "Always Restart",
	on_failure: "Restarts on Failure",
	never: "Never Restarts",
};

export const STARTUP_WORDS: Record<string, string> = {
	automatic: "Automatic Startup",
	manual: "Manual Startup",
};

/** Human uptime from an attempt start timestamp (client clock). */
export function formatServiceUptime(
	startedAt: string | null | undefined,
	nowMs: number = Date.now(),
): string {
	if (!startedAt) return "—";
	const elapsedMs = nowMs - Date.parse(startedAt);
	if (!Number.isFinite(elapsedMs) || elapsedMs < 0) return "—";
	const minutes = Math.floor(elapsedMs / 60_000);
	if (minutes < 1) return "<1m";
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return `${hours}h ${minutes % 60}m`;
	const days = Math.floor(hours / 24);
	return `${days}d ${hours % 24}h`;
}

/** Human memory from worker-heartbeat megabytes (client clock beats ~10s live). */
export function formatServiceMemory(
	mb: number | null | undefined,
): string {
	if (mb == null || !Number.isFinite(mb) || mb < 0) return "—";
	if (mb < 1024) return `${Math.round(mb)} MB`;
	return `${(mb / 1024).toFixed(1)} GB`;
}

/** Observed-state label matching the list and detail badges. */
export function formatObservedState(state: string): string {
	if (state === "starting") return "Starting";
	if (state === "restarting") return "Restarting";
	if (state === "stopping") return "Stopping";
	if (state === "crash_loop") return "Crash loop";
	if (state === "stopped") return "Stopped";
	return "Running";
}

function ObservedStateBadge({ state }: { state: string }) {
	if (state === "running") {
		return (
			<Badge
				variant="default"
				className="bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
			>
				Running
			</Badge>
		);
	}
	if (state === "starting" || state === "restarting") {
		return (
			<Badge
				variant="secondary"
				className="bg-[var(--bf-info-soft)] text-[var(--bf-info)]"
			>
				{state === "starting" ? "Starting" : "Restarting"}
			</Badge>
		);
	}
	if (state === "stopping") {
		return (
			<Badge
				variant="outline"
				className="bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]"
			>
				Stopping
			</Badge>
		);
	}
	if (state === "crash_loop") {
		return (
			<Badge variant="destructive">
				<AlertTriangle className="mr-1 h-3 w-3" />
				Crash loop
			</Badge>
		);
	}
	return <Badge variant="outline">Stopped</Badge>;
}

export function ServiceListSurface({
	services,
	viewMode,
	isLoading = false,
	isPlatformAdmin,
	getOrgName,
	onSelect,
	getDetailHref,
	onAction,
	emptySearchActive = false,
}: ServiceListSurfaceProps) {
	const navigate = useNavigate();

	const openService = (service: ServiceListItem) => {
		const href = getDetailHref?.(service);
		if (href) {
			navigate(href);
			return;
		}
		onSelect?.(service);
	};

	if (isLoading) {
		return viewMode === "grid" ? (
			<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))]">
				{[...Array(6)].map((_, i) => (
					<Skeleton key={i} className="h-56 w-full" />
				))}
			</div>
		) : (
			<div className="space-y-2">
				{[...Array(3)].map((_, i) => (
					<Skeleton key={i} className="h-12 w-full" />
				))}
			</div>
		);
	}

	if (services.length === 0) {
		return (
			<Card>
				<CardContent className="flex flex-col items-center justify-center py-12 text-center">
					<SlidersHorizontal className="h-12 w-12 text-muted-foreground" />
					<h3 className="mt-4 text-lg font-semibold">
						{emptySearchActive
							? "No services match your filters"
							: "No services registered"}
					</h3>
					<p className="mt-2 text-sm text-muted-foreground">
						{emptySearchActive
							? "Adjust your search or filters to see more services."
							: "Services appear here once a @service function is discovered"}
					</p>
				</CardContent>
			</Card>
		);
	}

	const renderActions = (service: ServiceListItem) => (
		<RecordActionsMenu label={`${service.workflow_name} actions`}>
			<DropdownMenuItem
				className="min-h-11"
				disabled={service.desired_state === "running"}
				onSelect={() => onAction?.(service, "start")}
			>
				<Play aria-hidden="true" className="size-4" />
				Start
			</DropdownMenuItem>
			<DropdownMenuItem
				className="min-h-11"
				disabled={service.desired_state === "stopped"}
				onSelect={() => onAction?.(service, "stop")}
			>
				<CircleStop aria-hidden="true" className="size-4" />
				Stop
			</DropdownMenuItem>
			<DropdownMenuItem
				className="min-h-11"
				disabled={!service.enabled}
				onSelect={() => onAction?.(service, "restart")}
			>
				<RotateCcw aria-hidden="true" className="size-4" />
				Restart
			</DropdownMenuItem>
			<DropdownMenuItem
				className="min-h-11"
				disabled={service.enabled}
				onSelect={() => onAction?.(service, "enable")}
			>
				<Play aria-hidden="true" className="size-4" />
				Enable
			</DropdownMenuItem>
			<DropdownMenuItem
				className="min-h-11"
				disabled={!service.enabled}
				onSelect={() => onAction?.(service, "disable")}
			>
				<OctagonX aria-hidden="true" className="size-4" />
				Disable
			</DropdownMenuItem>
		</RecordActionsMenu>
	);

	const renderStateCell = (service: ServiceListItem) => (
		<ObservedStateBadge state={service.observed_state} />
	);

	if (viewMode === "table") {
		return (
			<div className="flex-1 min-h-0">
				<DataTable className="max-h-full">
					<DataTableHeader>
						<DataTableRow>
							{isPlatformAdmin && (
								<DataTableHead className="w-0 whitespace-nowrap">
									Organization
								</DataTableHead>
							)}
							<DataTableHead>Service</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								State
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Uptime
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Memory
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap text-right">
								<span className="sr-only">Actions</span>
							</DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{services.map((service) => {
							const detailHref = getDetailHref?.(service);
							const selectable = Boolean(detailHref || onSelect);
							return (
								<DataTableRow
									key={service.id}
									clickable={selectable}
									href={detailHref}
									onClick={
										selectable
											? () => openService(service)
											: undefined
									}
								>
								{isPlatformAdmin && (
										<DataTableCell className="w-0 whitespace-nowrap align-middle text-sm text-muted-foreground">
											{getOrgName(
												service.organization_id,
											)}
										</DataTableCell>
									)}
									<DataTableCell className="min-w-0 whitespace-normal align-middle">
										{detailHref ? (
											<Link
												to={detailHref}
												className="inline-flex min-h-11 min-w-0 items-center font-mono font-medium text-left [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
											>
												{service.workflow_name}
											</Link>
										) : (
											<span className="inline-flex min-h-11 min-w-0 items-center font-mono font-medium text-left [overflow-wrap:anywhere]">
												{service.workflow_name}
											</span>
										)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap align-middle">
										{renderStateCell(service)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap align-middle text-sm">
										{formatServiceUptime(
											service.active_attempt?.started_at,
										)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap align-middle text-sm tabular-nums">
										{formatServiceMemory(service.memory_mb)}
									</DataTableCell>
									<DataTableCell
										className="w-0 whitespace-nowrap text-right"
										onClick={(event) =>
											event.stopPropagation()
										}
									>
										{renderActions(service)}
									</DataTableCell>
								</DataTableRow>
							);
						})}
					</DataTableBody>
				</DataTable>
			</div>
		);
	}

	return (
		<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))]">
			{services.map((service) => {
				const detailHref = getDetailHref?.(service);
				return (
					<ResourceCatalogCard
						key={service.id}
						icon={
							<span className="inline-grid size-12 shrink-0 place-items-center overflow-hidden rounded-[var(--bf-radius-control)] border border-primary/15 bg-primary/10 text-primary">
								<Radio aria-hidden="true" className="size-6" />
							</span>
						}
						title={service.workflow_name}
						subtitle={
							<ObservedStateBadge
								state={service.observed_state}
							/>
						}
						description={`${RESTART_WORDS[service.restart_policy] ?? service.restart_policy} · ${STARTUP_WORDS[service.startup_policy] ?? service.startup_policy}`}
						action={renderActions(service)}
						footer={
							<div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
								{isPlatformAdmin && (
									<span className="truncate">
										{getOrgName(service.organization_id)}
									</span>
								)}
								<span>
									uptime{" "}
									{formatServiceUptime(
										service.active_attempt?.started_at,
									)}
								</span>
								<span className="tabular-nums">
									{formatServiceMemory(service.memory_mb)}
								</span>
							</div>
						}
						onOpen={() => openService(service)}
						href={detailHref}
						disabled={!detailHref && !onSelect}
					/>
				);
			})}
		</div>
	);
}
