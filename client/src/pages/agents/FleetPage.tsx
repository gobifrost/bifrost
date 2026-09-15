import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { FleetReviewDialog } from "./FleetReviewDialog";
import { FleetHeader } from "./FleetHeader";
import { FleetToolbar } from "./FleetToolbar";
import { FleetReadError } from "./FleetReadError";
import { useMediaQuery } from "@/hooks/useMediaQuery";
/**
 * FleetPage — fleet-wide view of all agents.
 *
 * Visual spec mirrors `/tmp/agent-mockup/src/pages/FleetPage.tsx`: stat row with
 * deltas, paired grid/table toggle, per-agent cards with mini-stat trio +
 * sparkline + footer row. Per-agent stats are included by the list endpoint so
 * the page renders the fleet in one bounded request instead of one call/card.
 */

import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
	Bot,
	Building2,
	Clock,
	Globe,
	Hash,
	MessageSquare,
	Phone,
	Pencil,
	Plus,
	Power,
} from "lucide-react";
import { AgentMcpCopyButton } from "./AgentMcpCopyButton";

import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { ResourceIcon } from "@/components/ResourceIcon";
import { ResourceCatalogCard } from "@/components/catalog/ResourceCatalogCard";
import { PageLoader } from "@/components/PageLoader";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";

import { QueueBanner } from "@/components/agents/QueueBanner";
import { Sparkline } from "@/components/agents/Sparkline";
import { FleetMetrics } from "./FleetMetrics";
import { SummaryBackfillButton } from "@/components/agents/SummaryBackfillButton";
import { useAuth } from "@/contexts/AuthContext";
import { term, useTerminology } from "@/lib/terminology";
import { useOrganizations } from "@/hooks/useOrganizations";
import type { components } from "@/lib/v1";
import {
	CARD_SURFACE,
	CHIP_OUTLINE,
	GAP_CARD,
	PILL_ACTIVE,
	RADIUS_CARD,
	TONE_MUTED,
	TYPE_MINI_STAT_VALUE,
	TYPE_MUTED,
	successRateTone,
} from "@/components/agents/design-tokens";

import { useAgents, type AgentSummary } from "@/hooks/useAgents";
import { useFleetStats } from "@/services/agents";
import {
	cn,
	formatCost,
	formatDuration,
	formatNumber,
	formatRelativeTime,
} from "@/lib/utils";
import { prefetchAgentDetail } from "@/lib/detail-route-loaders";

type ViewMode = "grid" | "table";
type Organization = components["schemas"]["OrganizationPublic"];

export function FleetPage() {
	const [reviewOpen, setReviewOpen] = useState(false);
	const [view, setView] = useState<ViewMode>("grid");
	const tableAvailable = useMediaQuery("(min-width: 1024px)");
	const [query, setQuery] = useState("");
	const [showInactive, setShowInactive] = useState(false);
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const { isPlatformAdmin } = useAuth();
	const terminology = useTerminology();

	const {
		data: agents,
		isLoading: agentsLoading,
		isError: agentsError,
		isFetching: agentsFetching,
		refetch: refetchAgents,
	} = useAgents(isPlatformAdmin ? filterOrgId : undefined, {
		includeInactive: showInactive,
		includeStats: true,
	});
	const {
		data: fleetStats,
		isLoading: fleetLoading,
		isError: fleetError,
		isFetching: fleetFetching,
		refetch: refetchFleet,
	} = useFleetStats();

	// Resolve organization names for badges (platform admins only).
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});
	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return org?.name || orgId;
	};

	const filtered = useMemo(() => {
		const q = query.trim().toLowerCase();
		if (!q) return agents ?? [];
		return (agents ?? []).filter((a) => {
			const name = (a.name ?? "").toLowerCase();
			const desc = (a.description ?? "").toLowerCase();
			return name.includes(q) || desc.includes(q);
		});
	}, [agents, query]);

	const totalAgents = agents?.length ?? 0;
	const activeCount = useMemo(
		() => (agents ?? []).filter((a) => a.is_active).length,
		[agents],
	);

	return (
		<PageWorkspace className="mx-auto flex min-w-0 max-w-[1400px] flex-col gap-4 md:gap-5">
			<div className="shrink-0 space-y-6">
				<FleetHeader
					title={term(terminology, "agent", "plural")}
					agentLabel={term(terminology, "agent", "singularLower")}
					total={totalAgents}
					active={activeCount}
					actions={isPlatformAdmin ? <SummaryBackfillButton /> : null}
				/>

				{reviewOpen && (
					<FleetReviewDialog onClose={() => setReviewOpen(false)} />
				)}
				{/* Tuning queue banner */}
				{fleetStats && fleetStats.needs_review > 0 ? (
					<QueueBanner
						count={fleetStats.needs_review}
						actionLabel="Review now"
						onAction={() => setReviewOpen(true)}
					/>
				) : null}

				{fleetError && (
					<FleetReadError
						resource="fleet statistics"
						cached={!!fleetStats}
						pending={fleetFetching}
						onRetry={() => {
							void refetchFleet();
						}}
					/>
				)}
				{!(fleetError && !fleetStats) && (
					<FleetMetrics
						stats={fleetStats}
						loading={fleetLoading}
						scopeLabel={
							isPlatformAdmin
								? "All organizations"
								: "Your organization"
						}
						agentLabel={term(terminology, "agent", "pluralLower")}
					/>
				)}

				<FleetToolbar
					agentLabel={term(terminology, "agent", "pluralLower")}
					query={query}
					filterOrgId={filterOrgId}
					showInactive={showInactive}
					view={view}
					isPlatformAdmin={isPlatformAdmin}
					onQueryChange={setQuery}
					onOrganizationChange={setFilterOrgId}
					onInactiveChange={setShowInactive}
					onViewChange={setView}
				/>
			</div>

			{/* Content */}
			<PageScrollArea
				className={
					view === "table" && tableAvailable
						? "lg:overflow-hidden"
						: undefined
				}
			>
				<div className="min-w-0 space-y-4 pb-1 lg:flex lg:h-full lg:min-h-0 lg:flex-col">
					{agentsError && (
						<FleetReadError
							resource={term(terminology, "agent", "pluralLower")}
							cached={!!agents}
							pending={agentsFetching}
							onRetry={() => {
								void refetchAgents();
							}}
						/>
					)}
					{agentsError && !agents ? null : agentsLoading ? (
						<PageLoader
							message={`Loading ${term(terminology, "agent", "pluralLower")}…`}
							size="sm"
						/>
					) : filtered.length === 0 ? (
						<EmptyState hasQuery={query.trim().length > 0} />
					) : view === "grid" || !tableAvailable ? (
						<div
							className={cn(
								"grid md:grid-cols-2 xl:grid-cols-3",
								GAP_CARD,
							)}
						>
							{filtered.map((agent) => (
								<AgentGridCard
									key={agent.id}
									agent={agent}
									showOrg={isPlatformAdmin}
									orgName={getOrgName(agent.organization_id)}
								/>
							))}
						</div>
					) : (
						<AgentTable
							agents={filtered}
							showOrg={isPlatformAdmin}
							getOrgName={getOrgName}
						/>
					)}
				</div>
			</PageScrollArea>
		</PageWorkspace>
	);
}

function EmptyState({ hasQuery }: { hasQuery: boolean }) {
	const terminology = useTerminology();

	return (
		<div className={cn(CARD_SURFACE, "py-12 text-center")}>
			<Bot className="mx-auto h-10 w-10 text-muted-foreground" />
			<h3 className="mt-3 text-[15px] font-semibold">
				{hasQuery
					? `No ${term(terminology, "agent", "pluralLower")} match your search`
					: `No ${term(terminology, "agent", "pluralLower")} yet`}
			</h3>
			<p className={cn("mt-1", TYPE_MUTED)}>
				{hasQuery
					? "Try adjusting your search."
					: `Get started by creating your first AI ${term(terminology, "agent", "singularLower")}.`}
			</p>
			{!hasQuery ? (
				<Button asChild variant="outline" size="sm" className="mt-4">
					<Link to="/agents/new">
						<Plus className="h-3.5 w-3.5" /> New{" "}
						{term(terminology, "agent", "singularLower")}
					</Link>
				</Button>
			) : null}
		</div>
	);
}

function ChannelBadge({ channel }: { channel: string }) {
	const Icon =
		channel === "voice"
			? Phone
			: channel === "teams" || channel === "slack"
				? Hash
				: MessageSquare;
	return (
		<span className={CHIP_OUTLINE}>
			<Icon className="h-3 w-3" /> {channel}
		</span>
	);
}

function AgentGridCard({
	agent,
	showOrg,
	orgName,
}: {
	agent: AgentSummary;
	showOrg: boolean;
	orgName: string;
}) {
	const stats = agent.stats;
	const successRate = stats?.success_rate ?? 0;
	const colorClass = successRateTone(successRate);
	const hasRuns = (stats?.runs_7d ?? 0) > 0;
	const navigate = useNavigate();
	const terminology = useTerminology();

	return (
		<div
			onPointerEnter={() => prefetchAgentDetail(agent.id)}
			onFocus={() => prefetchAgentDetail(agent.id)}
		>
			<ResourceCatalogCard
				icon={
					<ResourceIcon
						kind="agent"
						id={agent.id}
						logo={agent.logo_url ?? null}
						size="card"
						className="border-violet-500/20 bg-violet-500/10 text-violet-700 dark:text-violet-300 [&_svg]:text-current"
					/>
				}
				title={agent.name}
				subtitle={
					<>
						{term(terminology, "agent", "singular")}
						<span> · </span>
						{agent.is_active ? "Active" : "Paused"}
					</>
				}
				description={agent.description}
				action={<AgentActions agent={agent} />}
				footer={
					showOrg ? (
						<p className="flex min-w-0 items-center gap-2">
							{agent.organization_id ? (
								<Building2 className="size-3.5 shrink-0" />
							) : (
								<Globe className="size-3.5 shrink-0" />
							)}
							<span className="truncate">{orgName}</span>
						</p>
					) : undefined
				}
				href={`/agents/${agent.id}`}
				onOpen={() => navigate(`/agents/${agent.id}`)}
			>
				<div className="flex flex-wrap items-center gap-1.5">
					{(agent.channels ?? []).slice(0, 3).map((c) => (
						<ChannelBadge key={c} channel={c} />
					))}
				</div>
				{hasRuns ? (
					<div className="mt-3 space-y-3 border-t pt-3">
						<div className="grid grid-cols-3 gap-3">
							<MiniStat
								label="Runs"
								value={formatNumber(stats!.runs_7d)}
							/>
							<MiniStat
								label="Success"
								value={`${Math.round(stats!.success_rate * 100)}%`}
								valueClass={colorClass}
							/>
							<MiniStat
								label="Spend"
								value={formatCost(stats!.total_cost_7d)}
							/>
						</div>
						{stats!.runs_by_day && stats!.runs_by_day.length > 1 ? (
							<div className="h-12">
								<Sparkline
									values={stats!.runs_by_day}
									colorClass={colorClass}
								/>
							</div>
						) : null}
						<div
							className={cn(
								"flex flex-wrap items-center justify-between gap-2 text-xs",
								TONE_MUTED,
							)}
						>
							<span className="inline-flex items-center gap-1">
								<Clock className="h-3 w-3" />
								{stats!.last_run_at
									? `Last run ${formatRelativeTime(stats!.last_run_at)}`
									: "—"}
							</span>
							<span>
								avg {formatDuration(stats!.avg_duration_ms)}
							</span>
						</div>
					</div>
				) : (
					<p className={cn("mt-3 border-t pt-3", TYPE_MUTED)}>
						No runs yet ·{" "}
						{agent.is_active ? "waiting for traffic" : "paused"}
					</p>
				)}
			</ResourceCatalogCard>
		</div>
	);
}

function OrgBadge({
	orgId,
	name,
}: {
	orgId: string | null | undefined;
	name: string;
}) {
	if (orgId) {
		return (
			<Badge
				variant="outline"
				className="min-w-0 whitespace-normal [overflow-wrap:anywhere] text-xs"
			>
				<Building2 className="mr-1 h-3 w-3" />
				{name}
			</Badge>
		);
	}
	return (
		<Badge variant="default" className="text-xs">
			<Globe className="mr-1 h-3 w-3" />
			Global
		</Badge>
	);
}

function MiniStat({
	label,
	value,
	valueClass,
}: {
	label: string;
	value: string;
	valueClass?: string;
}) {
	return (
		<div>
			<div className="mb-0.5 text-[11px] text-muted-foreground">
				{label}
			</div>
			<div className={cn(TYPE_MINI_STAT_VALUE, valueClass)}>{value}</div>
		</div>
	);
}

function AgentTable({
	agents,
	showOrg,
	getOrgName,
}: {
	agents: AgentSummary[];
	showOrg: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
}) {
	return (
		<div
			className={cn(
				"min-h-0 max-h-full overflow-hidden border",
				RADIUS_CARD,
			)}
		>
			<DataTable>
				<DataTableHeader>
					<DataTableRow>
						{showOrg && (
							<DataTableHead className="w-0 whitespace-nowrap">
								Organization
							</DataTableHead>
						)}
						<DataTableHead>Name</DataTableHead>
						<DataTableHead>Channels</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap text-right">
							Runs (7d)
						</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap text-right">
							Success
						</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap text-right">
							Spend (7d)
						</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap">
							Last run
						</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap">
							Status
						</DataTableHead>
						<DataTableHead className="w-0">
							<span className="sr-only">Actions</span>
						</DataTableHead>
					</DataTableRow>
				</DataTableHeader>
				<DataTableBody>
					{agents.map((agent) => (
						<AgentTableRow
							key={agent.id}
							agent={agent}
							showOrg={showOrg}
							orgName={getOrgName(agent.organization_id)}
						/>
					))}
				</DataTableBody>
			</DataTable>
		</div>
	);
}

function AgentTableRow({
	agent,
	showOrg,
	orgName,
}: {
	agent: AgentSummary;
	showOrg: boolean;
	orgName: string;
}) {
	const navigate = useNavigate();
	const stats = agent.stats;
	const hasRuns = (stats?.runs_7d ?? 0) > 0;

	return (
		<DataTableRow
			href={`/agents/${agent.id}`}
			className="cursor-pointer hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
			onPointerEnter={() => prefetchAgentDetail(agent.id)}
			onFocus={() => prefetchAgentDetail(agent.id)}
			onClick={(event) => {
				if ((event.target as HTMLElement).closest("a, button")) return;
				navigate(`/agents/${agent.id}`);
			}}
		>
			{showOrg && (
				<DataTableCell className="w-0 whitespace-nowrap">
					<OrgBadge orgId={agent.organization_id} name={orgName} />
				</DataTableCell>
			)}
			<DataTableCell>
				<div className="flex items-center gap-2">
					<ResourceIcon
						kind="agent"
						id={agent.id}
						logo={agent.logo_url ?? null}
						size="table"
					/>
					<Link
						to={`/agents/${agent.id}`}
						className="inline-flex min-h-11 items-center font-medium [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					>
						{agent.name}
					</Link>
				</div>
				{agent.description ? (
					<div className="line-clamp-1 text-xs text-muted-foreground">
						{agent.description}
					</div>
				) : null}
			</DataTableCell>
			<DataTableCell>
				<div className="flex flex-wrap gap-1">
					{(agent.channels ?? []).map((c) => (
						<ChannelBadge key={c} channel={c} />
					))}
				</div>
			</DataTableCell>
			<DataTableCell className="w-0 whitespace-nowrap text-right tabular-nums">
				{hasRuns ? formatNumber(stats!.runs_7d) : "—"}
			</DataTableCell>
			<DataTableCell className="w-0 whitespace-nowrap text-right tabular-nums">
				{hasRuns ? `${Math.round(stats!.success_rate * 100)}%` : "—"}
			</DataTableCell>
			<DataTableCell className="w-0 whitespace-nowrap text-right tabular-nums">
				{hasRuns ? formatCost(stats!.total_cost_7d) : "—"}
			</DataTableCell>
			<DataTableCell className="w-0 whitespace-nowrap text-muted-foreground">
				{hasRuns && stats!.last_run_at
					? formatRelativeTime(stats!.last_run_at)
					: "—"}
			</DataTableCell>
			<DataTableCell className="w-0 whitespace-nowrap">
				{agent.is_active ? (
					<span className={PILL_ACTIVE}>
						<Power className="h-3 w-3" /> Active
					</span>
				) : (
					<Badge variant="secondary">Paused</Badge>
				)}
			</DataTableCell>
			<DataTableCell className="w-0 whitespace-nowrap text-right">
				<AgentActions agent={agent} />
			</DataTableCell>
		</DataTableRow>
	);
}

function AgentActions({ agent }: { agent: AgentSummary }) {
	const { isPlatformAdmin } = useAuth();
	return (
		<div className="flex items-center justify-end gap-1">
			{agent.is_solution_managed && (
				<SolutionManagedBadge solutionId={agent.solution_id} />
			)}
			<RecordActionsMenu label={`${agent.name} actions`}>
				{isPlatformAdmin && !agent.is_solution_managed && (
					<DropdownMenuItem asChild>
						<Link to={`/agents/${agent.id}?tab=settings`}>
							<Pencil aria-hidden="true" className="size-4" />
							Edit Agent
						</Link>
					</DropdownMenuItem>
				)}
				<AgentMcpCopyButton agentId={agent.id} variant="menuitem" />
			</RecordActionsMenu>
		</div>
	);
}

export default FleetPage;
