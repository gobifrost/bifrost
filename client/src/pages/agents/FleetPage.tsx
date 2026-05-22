/**
 * FleetPage — fleet-wide view of all agents.
 *
 * Visual spec mirrors `/tmp/agent-mockup/src/pages/FleetPage.tsx`: stat row with
 * deltas, paired grid/table toggle, per-agent cards with mini-stat trio +
 * sparkline + footer row. Per-agent stats are fetched via `useAgentStats(id)`
 * (N+1; acceptable v1, TODO for a denormalized list endpoint).
 */

import { type MouseEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
	AlertTriangle,
	Bot,
	Building2,
	Clock,
	Copy,
	Globe,
	Hash,
	History,
	LayoutGrid,
	List,
	MessageSquare,
	Phone,
	Plus,
	Power,
	Search,
} from "lucide-react";
import { toast } from "sonner";

import { EntityLogo } from "@/components/EntityLogo";
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
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

import { QueueBanner } from "@/components/agents/QueueBanner";
import { Sparkline } from "@/components/agents/Sparkline";
import { StatCard } from "@/components/agents/StatCard";
import { SummaryBackfillButton } from "@/components/agents/SummaryBackfillButton";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import type { components } from "@/lib/v1";
import {
	CARD_HOVER,
	CARD_SURFACE,
	CHIP_OUTLINE,
	GAP_CARD,
	PILL_ACTIVE,
	RADIUS_CARD,
	TONE_MUTED,
	TYPE_BODY,
	TYPE_CARD_TITLE,
	TYPE_MINI_STAT_VALUE,
	TYPE_MUTED,
	TYPE_PAGE_TITLE,
	successRateTone,
} from "@/components/agents/design-tokens";

import { useAgents, type AgentSummary } from "@/hooks/useAgents";
import { useAgentStats, useFleetStats } from "@/services/agents";
import {
	cn,
	formatCost,
	formatDuration,
	formatNumber,
	formatRelativeTime,
} from "@/lib/utils";

type ViewMode = "grid" | "table";
type Organization = components["schemas"]["OrganizationPublic"];

export function FleetPage() {
	const [view, setView] = useState<ViewMode>("grid");
	const [query, setQuery] = useState("");
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const { isPlatformAdmin } = useAuth();

	// Fleet view shows paused agents too (they need to be visible to un-pause).
	const { data: agents, isLoading: agentsLoading } = useAgents(
		isPlatformAdmin ? filterOrgId : undefined,
		{ includeInactive: true },
	);
	const { data: fleetStats, isLoading: fleetLoading } = useFleetStats();

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
		<div className="mx-auto flex h-full max-w-[1400px] flex-col gap-5">
			{/* Header */}
			<div className="flex items-start justify-between gap-4">
				<div>
					<h1 className={TYPE_PAGE_TITLE}>Agents</h1>
					<p className={cn("mt-1", TYPE_BODY, TONE_MUTED)}>
						{totalAgents} total · {activeCount} active · last 7 days
					</p>
				</div>
				<div className="flex items-center gap-2">
					<Button asChild variant="outline" size="sm">
						<Link to="/history?type=agents">
							<History className="h-3.5 w-3.5" /> All runs
						</Link>
					</Button>
					{isPlatformAdmin ? <SummaryBackfillButton /> : null}
					<Button asChild size="sm">
						<Link to="/agents/new">
							<Plus className="h-3.5 w-3.5" /> New agent
						</Link>
					</Button>
				</div>
			</div>

			{/* Tuning queue banner */}
			{fleetStats && fleetStats.needs_review > 0 ? (
				<QueueBanner
					count={fleetStats.needs_review}
					actionLabel="Review now"
					actionHref="/agents"
				/>
			) : null}

			{/* Fleet stats — 4 stats + red "Needs review" */}
			{fleetLoading || !fleetStats ? (
				<div
					className={cn(
						"grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-5",
						GAP_CARD,
					)}
				>
					{[...Array(5)].map((_, i) => (
						<Skeleton key={i} className="h-24 w-full" />
					))}
				</div>
			) : (
				<div
					className={cn(
						"grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-5",
						GAP_CARD,
					)}
				>
					<StatCard
						label="Runs (7d)"
						value={formatNumber(fleetStats.total_runs)}
						delta={
							fleetStats.total_runs > 0
								? "Across active agents"
								: "No runs yet"
						}
					/>
					<StatCard
						label="Success rate"
						value={`${Math.round((fleetStats.avg_success_rate ?? 0) * 100)}%`}
						delta="Across active agents"
					/>
					<StatCard
						label="Spend (7d)"
						value={formatCost(fleetStats.total_cost_7d)}
						delta={
							fleetStats.total_runs > 0
								? `${formatCost(
										Number(fleetStats.total_cost_7d) / 7,
									)}/day avg`
								: "—"
						}
					/>
					<StatCard
						label="Active agents"
						value={formatNumber(fleetStats.active_agents)}
						delta={`of ${totalAgents} total`}
					/>
					<StatCard
						label="Needs review"
						value={formatNumber(fleetStats.needs_review)}
						alert={fleetStats.needs_review > 0}
						icon={
							fleetStats.needs_review > 0 ? (
								<AlertTriangle className="h-[11px] w-[11px]" />
							) : undefined
						}
						delta={
							fleetStats.needs_review > 0
								? "runs marked — click to open"
								: "All runs reviewed"
						}
						deltaTone={fleetStats.needs_review > 0 ? "down" : "up"}
					/>
				</div>
			)}

			{/* Search + view toggle */}
			<div className="flex items-center justify-between gap-3">
				<div className="flex flex-1 items-center gap-3">
					<div className="relative max-w-md flex-1">
						<Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
						<Input
							aria-label="Search agents"
							placeholder="Search agents…"
							value={query}
							onChange={(e) => setQuery(e.target.value)}
							className="h-8 pl-8 text-[13px]"
						/>
					</div>
					{isPlatformAdmin && (
						<div className="w-64">
							<OrganizationSelect
								value={filterOrgId}
								onChange={setFilterOrgId}
								showAll
								showGlobal
								placeholder="All organizations"
							/>
						</div>
					)}
				</div>
				<div className="inline-flex items-center overflow-hidden rounded-md border">
					<button
						type="button"
						aria-label="Grid view"
						aria-pressed={view === "grid"}
						onClick={() => setView("grid")}
						className={cn(
							"inline-flex items-center gap-1.5 border-r px-3 py-1.5 text-[12.5px] transition-colors",
							view === "grid"
								? "bg-card text-foreground"
								: "text-muted-foreground hover:bg-accent/40",
						)}
					>
						<LayoutGrid className="h-3 w-3" /> Grid
					</button>
					<button
						type="button"
						aria-label="Table view"
						aria-pressed={view === "table"}
						onClick={() => setView("table")}
						className={cn(
							"inline-flex items-center gap-1.5 px-3 py-1.5 text-[12.5px] transition-colors",
							view === "table"
								? "bg-card text-foreground"
								: "text-muted-foreground hover:bg-accent/40",
						)}
					>
						<List className="h-3 w-3" /> Table
					</button>
				</div>
			</div>

			{/* Content */}
			<div className="flex-1 min-h-0 overflow-auto">
				{agentsLoading ? (
					view === "grid" ? (
						<div className={cn("grid md:grid-cols-2 xl:grid-cols-3", GAP_CARD)}>
							{[...Array(6)].map((_, i) => (
								<Skeleton key={i} className="h-52 w-full" />
							))}
						</div>
					) : (
						<div className="space-y-2">
							{[...Array(3)].map((_, i) => (
								<Skeleton key={i} className="h-12 w-full" />
							))}
						</div>
					)
				) : filtered.length === 0 ? (
					<EmptyState hasQuery={query.trim().length > 0} />
				) : view === "grid" ? (
					<div className={cn("grid md:grid-cols-2 xl:grid-cols-3", GAP_CARD)}>
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
		</div>
	);
}

function EmptyState({ hasQuery }: { hasQuery: boolean }) {
	return (
		<div className={cn(CARD_SURFACE, "py-12 text-center")}>
			<Bot className="mx-auto h-10 w-10 text-muted-foreground" />
			<h3 className="mt-3 text-[15px] font-semibold">
				{hasQuery ? "No agents match your search" : "No agents yet"}
			</h3>
			<p className={cn("mt-1", TYPE_MUTED)}>
				{hasQuery
					? "Try adjusting your search."
					: "Get started by creating your first AI agent."}
			</p>
			{!hasQuery ? (
				<Button asChild variant="outline" size="sm" className="mt-4">
					<Link to="/agents/new">
						<Plus className="h-3.5 w-3.5" /> New agent
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
	// TODO(plan-2): replace per-card useAgentStats N+1 with a denormalized
	// list endpoint that returns fleet member stats in one round-trip.
	const { data: stats, isLoading } = useAgentStats(agent.id ?? undefined);
	const successRate = stats?.success_rate ?? 0;
	const colorClass = successRateTone(successRate);
	const hasRuns = (stats?.runs_7d ?? 0) > 0;

	return (
		<Link
			to={`/agents/${agent.id}`}
			className={cn(
				"group flex flex-col overflow-hidden",
				CARD_SURFACE,
				CARD_HOVER,
			)}
		>
			<div className="border-b px-4 pb-3 pt-3.5">
				<div className="flex items-start justify-between gap-3">
					<div className="flex min-w-0 items-center gap-2">
						<EntityLogo
							entityType="agent"
							entityId={agent.id}
							logo={agent.logo ?? null}
							fallback={<Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
							size={20}
							className="h-5 w-5 rounded shrink-0 object-cover"
						/>
						<span className={cn("truncate", TYPE_CARD_TITLE)}>
							{agent.name}
						</span>
						{!agent.is_active ? (
							<Badge variant="secondary" className="text-[11px]">
								Paused
							</Badge>
						) : null}
					</div>
					<div className="flex shrink-0 flex-wrap gap-1">
						{(agent.channels ?? []).slice(0, 3).map((c) => (
							<ChannelBadge key={c} channel={c} />
						))}
					</div>
				</div>
				{agent.description ? (
					<p className={cn("mt-1 line-clamp-2", TYPE_MUTED)}>
						{agent.description}
					</p>
				) : null}
			</div>
			<div className="flex-1 space-y-3 p-4">
				{isLoading ? (
					<Skeleton className="h-24 w-full" />
				) : hasRuns ? (
					<>
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
								"flex items-center justify-between text-[12px]",
								TONE_MUTED,
							)}
						>
							<span className="inline-flex items-center gap-1">
								<Clock className="h-3 w-3" />
								{stats!.last_run_at
									? `Last run ${formatRelativeTime(stats!.last_run_at)}`
									: "—"}
							</span>
							<span>avg {formatDuration(stats!.avg_duration_ms)}</span>
						</div>
					</>
				) : (
					<p className={cn("py-1", TYPE_MUTED)}>
						No runs yet ·{" "}
						{agent.is_active ? "waiting for traffic" : "paused"}
					</p>
				)}
			</div>
			<div className="flex items-center justify-between gap-2 border-t px-4 py-2.5">
				{showOrg ? (
					<OrgBadge orgId={agent.organization_id} name={orgName} />
				) : (
					<span />
				)}
				{agent.id ? <McpUrlBadge agentId={agent.id} /> : null}
			</div>
		</Link>
	);
}

function McpUrlBadge({ agentId }: { agentId: string }) {
	const url = `${window.location.origin}/mcp/${agentId}`;
	const handleCopy = (e: MouseEvent) => {
		e.preventDefault();
		e.stopPropagation();
		void navigator.clipboard.writeText(url);
		toast.success("Agent MCP URL copied");
	};
	return (
		<button
			type="button"
			onClick={handleCopy}
			title={url}
			aria-label="Copy agent MCP URL"
			data-testid="agent-mcp-copy"
			className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
		>
			MCP
			<Copy className="h-3 w-3" />
		</button>
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
			<Badge variant="outline" className="text-xs">
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
			<div className={cn(TYPE_MINI_STAT_VALUE, valueClass)}>
				{value}
			</div>
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
		<div className={cn("overflow-hidden border", RADIUS_CARD)}>
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
	const { data: stats } = useAgentStats(agent.id ?? undefined);
	const hasRuns = (stats?.runs_7d ?? 0) > 0;

	return (
		<DataTableRow
			className="cursor-pointer hover:bg-accent/40"
			onClick={() => {
				window.location.href = `/agents/${agent.id}`;
			}}
		>
			{showOrg && (
				<DataTableCell className="w-0 whitespace-nowrap">
					<OrgBadge orgId={agent.organization_id} name={orgName} />
				</DataTableCell>
			)}
			<DataTableCell>
				<div className="flex items-center gap-2">
					<Bot className="h-3.5 w-3.5 text-muted-foreground" />
					<span className="font-medium">{agent.name}</span>
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
		</DataTableRow>
	);
}

export default FleetPage;
