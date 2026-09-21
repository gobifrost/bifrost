import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search, Sparkles } from "lucide-react";

import { FleetReadError } from "./FleetReadError";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents } from "@/hooks/useAgents";
import { agentPlatform } from "@/services/agentPlatform";
import type { components } from "@/lib/v1";

type Collection = "findings" | "tests" | "reviews";
type AgentSummary = { id: string; name?: string | null };
type Finding = components["schemas"]["FindingPublic"];
type AgentTest = components["schemas"]["AgentTestPublic"];
type Review = components["schemas"]["AgentReviewDefinitionPublic"];

const COLLECTIONS: Collection[] = ["findings", "tests", "reviews"];

export function GlobalAgentQualityPage() {
	const [params, setParams] = useSearchParams();
	const collection = normalizeCollection(params.get("collection"));
	const selectedAgentId = params.get("agent") ?? "";
	const search = params.get("search") ?? "";
	const status = params.get("status") ?? "";
	const kind = params.get("kind") ?? "";

	const { data: agentList } = useAgents(undefined, {
		includeInactive: true,
		includeStats: false,
	});
	const agents = (agentList ?? []) as AgentSummary[];
	const agentName = (agentId: string) =>
		agents.find((agent) => agent.id === agentId)?.name ?? agentId;

	function update(values: Record<string, string | undefined>) {
		const next = new URLSearchParams(params);
		Object.entries(values).forEach(([key, value]) => {
			if (value) next.set(key, value);
			else next.delete(key);
		});
		setParams(next, { replace: true });
	}

	const findings = useQuery({
		queryKey: [
			"agent-quality",
			"findings",
			search,
			status,
			kind,
			selectedAgentId,
		],
		queryFn: () =>
			agentPlatform.searchFindings({
				offset: 0,
				limit: 50,
				q: search || undefined,
				status: status || undefined,
				finding_kind: kind || undefined,
				agent_id: selectedAgentId || undefined,
			}),
		enabled: collection === "findings",
	});
	const tests = useQuery({
		queryKey: ["agent-quality", "tests", selectedAgentId],
		queryFn: () =>
			agentPlatform.agentTests(selectedAgentId, {
				offset: 0,
				limit: 50,
			}),
		enabled: collection === "tests" && !!selectedAgentId,
	});
	const reviews = useQuery({
		queryKey: ["agent-quality", "reviews", selectedAgentId],
		queryFn: () =>
			agentPlatform.reviews({
				agent_id: selectedAgentId,
				offset: 0,
				limit: 50,
			}),
		enabled: collection === "reviews" && !!selectedAgentId,
	});

	return (
		<PageWorkspace className="mx-auto flex min-w-0 w-full max-w-[1200px] flex-col gap-5">
			<div className="shrink-0 space-y-5">
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div className="min-w-0">
						<h1 className="font-display text-2xl font-semibold tracking-tight">
							Agent quality
						</h1>
						<p className="mt-1 text-sm text-muted-foreground">
							Findings, tests, and reviews across the agent fleet.
						</p>
					</div>
					<Button asChild variant="outline">
						<Link to="/agents">
							<Sparkles aria-hidden="true" className="size-4" />
							Fleet
						</Link>
					</Button>
				</div>

				<div className="flex flex-wrap items-center gap-3">
					<Select
						value={collection}
						onValueChange={(value) =>
							update({
								collection: value,
								agent:
									value === "findings"
										? selectedAgentId || undefined
										: selectedAgentId,
							})
						}
					>
						<SelectTrigger
							aria-label="Quality collection"
							className="min-h-11 w-full sm:w-[180px]"
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="findings">Findings</SelectItem>
							<SelectItem value="tests">Tests</SelectItem>
							<SelectItem value="reviews">Reviews</SelectItem>
						</SelectContent>
					</Select>

					<Select
						value={selectedAgentId || "all"}
						onValueChange={(value) =>
							update({ agent: value === "all" ? undefined : value })
						}
					>
						<SelectTrigger
							aria-label="Agent filter"
							className="min-h-11 w-full sm:w-[220px]"
						>
							<SelectValue placeholder="All agents" />
						</SelectTrigger>
						<SelectContent>
							{collection === "findings" ? (
								<SelectItem value="all">All agents</SelectItem>
							) : null}
							{agents.map((agent) => (
								<SelectItem key={agent.id} value={agent.id}>
									{agent.name ?? agent.id}
								</SelectItem>
							))}
						</SelectContent>
					</Select>

					<div className="relative min-w-0 flex-[1_1_14rem] max-w-sm">
						<Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
						<Input
							aria-label="Search quality"
							value={search}
							onChange={(event) =>
								update({
									search: event.target.value || undefined,
								})
							}
							placeholder="Search quality"
							className="min-h-11 pl-8"
						/>
					</div>
				</div>
			</div>

			<PageScrollArea className="min-w-0 space-y-3">
				{collection === "findings" ? (
					<FindingsList
						query={findings}
						agentName={agentName}
						params={params}
					/>
				) : selectedAgentId ? (
					collection === "tests" ? (
						<TestsList
							query={tests}
							agentId={selectedAgentId}
							params={params}
						/>
					) : (
						<ReviewsList
							query={reviews}
							agentId={selectedAgentId}
							params={params}
						/>
					)
				) : (
					<p className="rounded-[var(--bf-radius-surface)] border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
						Select an agent to view {collection}.
					</p>
				)}
			</PageScrollArea>
		</PageWorkspace>
	);
}

function FindingsList({
	query,
	agentName,
	params,
}: {
	query: ReturnType<typeof useQuery>;
	agentName: (agentId: string) => string;
	params: URLSearchParams;
}) {
	if (query.isLoading) return <LoadingRows />;
	if (query.isError)
		return (
			<FleetReadError
				resource="quality findings"
				cached={false}
				pending={query.isFetching}
				onRetry={() => void query.refetch()}
			/>
		);
	const items = (((query.data as { items?: Finding[] } | undefined)?.items ??
		[]) as Finding[]);
	if (items.length === 0) return <Empty label="No findings match this filter." />;
	return (
		<div className="space-y-2">
			{items.map((finding) => (
				<QualityRow
					key={finding.id}
					title={finding.description}
					meta={`${agentName(finding.agent_id)} · ${finding.source_kind} source · ${finding.status}`}
					to={qualityHref(finding.agent_id, params, {
						collection: "findings",
						finding: finding.id,
						selected: `findings:${finding.id}`,
					})}
					label={`Open finding ${finding.description}`}
				/>
			))}
		</div>
	);
}

function TestsList({
	query,
	agentId,
	params,
}: {
	query: ReturnType<typeof useQuery>;
	agentId: string;
	params: URLSearchParams;
}) {
	if (query.isLoading) return <LoadingRows />;
	if (query.isError)
		return (
			<FleetReadError
				resource="agent tests"
				cached={false}
				pending={query.isFetching}
				onRetry={() => void query.refetch()}
			/>
		);
	const items = (((query.data as { items?: AgentTest[] } | undefined)?.items ??
		[]) as AgentTest[]);
	if (items.length === 0) return <Empty label="No tests for this agent." />;
	return (
		<div className="space-y-2">
			{items.map((test) => (
				<QualityRow
					key={test.logical_test_id}
					title={test.name}
					meta={`${test.origin_suite_name} · v${test.version} · ${test.enabled ? "enabled" : "disabled"}`}
					to={qualityHref(agentId, params, {
						collection: "tests",
						test: test.logical_test_id,
					})}
					label={`Open test ${test.name}`}
				/>
			))}
		</div>
	);
}

function ReviewsList({
	query,
	agentId,
	params,
}: {
	query: ReturnType<typeof useQuery>;
	agentId: string;
	params: URLSearchParams;
}) {
	if (query.isLoading) return <LoadingRows />;
	if (query.isError)
		return (
			<FleetReadError
				resource="agent reviews"
				cached={false}
				pending={query.isFetching}
				onRetry={() => void query.refetch()}
			/>
		);
	const items = (((query.data as { items?: Review[] } | undefined)?.items ??
		[]) as Review[]);
	if (items.length === 0) return <Empty label="No reviews for this agent." />;
	return (
		<div className="space-y-2">
			{items.map((review) => (
				<QualityRow
					key={review.id}
					title={review.name}
					meta={`${review.status} · v${review.latest_version}`}
					to={qualityHref(agentId, params, {
						collection: "reviews",
						review: review.id,
					})}
					label={`Open review ${review.name}`}
				/>
			))}
		</div>
	);
}

function QualityRow({
	title,
	meta,
	to,
	label,
}: {
	title: string;
	meta: string;
	to: string;
	label: string;
}) {
	return (
		<div className="rounded-[var(--bf-radius-surface)] border bg-card p-4">
			<div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
				<div className="min-w-0">
					<p className="font-medium [overflow-wrap:anywhere]">{title}</p>
					<p className="mt-1 text-xs text-muted-foreground">{meta}</p>
				</div>
				<Button asChild variant="outline" size="sm">
					<Link aria-label={label} to={to}>
						Open
					</Link>
				</Button>
			</div>
		</div>
	);
}

function LoadingRows() {
	return (
		<div className="space-y-2">
			<Skeleton className="h-20 w-full" />
			<Skeleton className="h-20 w-full" />
		</div>
	);
}

function Empty({ label }: { label: string }) {
	return (
		<p className="rounded-[var(--bf-radius-surface)] border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
			{label}
		</p>
	);
}

function qualityHref(
	agentId: string,
	current: URLSearchParams,
	values: Record<string, string>,
) {
	const params = new URLSearchParams(current);
	params.delete("agent");
	params.delete("search");
	params.delete("status");
	params.delete("kind");
	Object.entries(values).forEach(([key, value]) => params.set(key, value));
	return `/agents/${agentId}/quality?${params.toString()}`;
}

function normalizeCollection(value: string | null): Collection {
	return COLLECTIONS.includes(value as Collection)
		? (value as Collection)
		: "findings";
}

export default GlobalAgentQualityPage;
