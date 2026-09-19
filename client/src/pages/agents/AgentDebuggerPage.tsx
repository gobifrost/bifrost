import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RunAIUsageCard } from "./RunAIUsageCard";
import { $api } from "@/lib/api-client";
import { agentPlatform } from "@/services/agentPlatform";
import { useAgentPlatformUpdates } from "@/hooks/useAgentPlatformUpdates";
import {
	EvidenceJson,
	PlatformError,
	PlatformStatus,
} from "@/components/agents/evaluation/PlatformEvidence";
import type { components } from "@/lib/v1";

function RunBranch({
	node,
	selected,
	agentId,
}: {
	node: components["schemas"]["AgentRunTreeNode"];
	selected: string;
	agentId: string;
}) {
	return (
		<li className="space-y-2">
			<Link
				aria-current={node.run_id === selected ? "page" : undefined}
				className="flex flex-wrap items-center gap-2 rounded-md p-2 text-sm hover:bg-muted aria-[current=page]:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
				to={`/agents/${node.agent_id ?? agentId}/runs/${node.run_id}/debug`}
			>
				<span className="break-all font-medium">
					{node.agent_name ?? "Agent run"}
				</span>
				<PlatformStatus status={node.status} />
				<span className="text-xs text-muted-foreground">
					Attempt {node.attempt}
				</span>
			</Link>
			{node.diagnostic && (
				<p role="status" className="text-sm">
					{node.diagnostic}
				</p>
			)}
			{!!node.children?.length && (
				<ul className="ml-3 space-y-2 border-l pl-3">
					{node.children.map((child) => (
						<RunBranch
							key={child.run_id}
							node={child}
							selected={selected}
							agentId={agentId}
						/>
					))}
				</ul>
			)}
		</li>
	);
}
export function AgentDebuggerPage() {
	const { agentId = "", runId = "" } = useParams();
	const [params] = useSearchParams();
	const sequence = Number(params.get("sequence")) || undefined;
	const [kind, setKind] = useState("");
	const [attempt, setAttempt] = useState("");
	useAgentPlatformUpdates(runId);
	const snapshot = useQuery({
		queryKey: ["agent-platform", runId, "snapshot"],
		queryFn: () => agentPlatform.snapshot(runId),
	});
	const tree = useQuery({
		queryKey: ["agent-platform", runId, "tree"],
		queryFn: () => agentPlatform.tree(runId),
	});
	const timeline = useInfiniteQuery({
		queryKey: [
			"agent-platform",
			runId,
			"timeline",
			kind,
			attempt,
			sequence,
		],
		initialPageParam: undefined as string | undefined,
		queryFn: ({ pageParam }) =>
			agentPlatform.timeline(runId, {
				cursor: pageParam,
				kind: kind || undefined,
				attempt: attempt ? Number(attempt) : undefined,
				limit: 50,
			}),
		getNextPageParam: (page) => page.next_cursor ?? undefined,
	});
	const checkpoints = useInfiniteQuery({
		queryKey: ["agent-platform", runId, "checkpoints"],
		initialPageParam: undefined as string | undefined,
		queryFn: ({ pageParam }) => agentPlatform.checkpoints(runId, pageParam),
		getNextPageParam: (page) => page.next_cursor ?? undefined,
	});
	const detail = $api.useQuery("get", "/api/agent-runs/{run_id}", {
		params: { path: { run_id: runId } },
	});
	const run = snapshot.data;
	const entries =
		timeline.data?.pages.flatMap((page) => page.entries ?? []) ?? [];
	const { hasNextPage, isFetching, isError, fetchNextPage } = timeline;
	const targetFound = entries.some((entry) => entry.sequence === sequence);
	useEffect(() => {
		if (sequence && !targetFound && hasNextPage && !isFetching && !isError)
			void fetchNextPage();
	}, [
		sequence,
		targetFound,
		hasNextPage,
		isFetching,
		isError,
		fetchNextPage,
	]);
	useEffect(() => {
		if (targetFound)
			document
				.getElementById(`sequence-${sequence}`)
				?.scrollIntoView({ block: "center" });
	}, [targetFound, sequence]);
	return (
		<div className="mx-auto w-full max-w-7xl space-y-6 pb-8">
			<Link
				className="inline-flex min-h-11 items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
				to={`/agents/${run?.agent_id ?? agentId}/runs/${runId}`}
			>
				<ArrowLeft aria-hidden="true" className="size-4" />
				Back to run
			</Link>
			<header className="flex flex-wrap items-start justify-between gap-4">
				<div>
					<h1 className="font-display text-2xl font-semibold">
						Run debugger
					</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						{run?.agent_name ?? "Agent run"} · Durable execution
						evidence
					</p>
					<p className="mt-2 break-all font-mono text-xs text-muted-foreground">
						{runId}
					</p>
				</div>
				<div className="flex flex-wrap items-center gap-2">
					{run && (
						<>
							<PlatformStatus status={run.status} />
							{run.attempt > 1 && (
								<PlatformStatus status="recovered" />
							)}
						</>
					)}
					<Button
						variant="outline"
						onClick={() => {
							void snapshot.refetch();
							void timeline.refetch();
							void checkpoints.refetch();
							void tree.refetch();
						}}
					>
						<RefreshCw aria-hidden="true" className="size-4" />
						Refresh evidence
					</Button>
				</div>
			</header>
			<PlatformError
				error={snapshot.error}
				retry={() => void snapshot.refetch()}
			/>
			{snapshot.isPending && <p role="status">Loading run snapshot…</p>}
			{run && (
				<>
					<div className="grid gap-6 lg:grid-cols-[minmax(220px,1fr)_minmax(0,3fr)]">
						<aside className="min-w-0 space-y-5">
							<section aria-label="Run tree">
								<h2 className="mb-2 text-sm font-semibold">
									Run tree
								</h2>
								<PlatformError
									error={tree.error}
									retry={() => void tree.refetch()}
								/>
								{tree.isPending && (
									<p role="status">Loading run tree…</p>
								)}
								{tree.data && (
									<>
										<ul
											aria-label="Delegated runs"
											className="max-h-[50vh] overflow-auto"
										>
											<RunBranch
												node={tree.data.root}
												selected={runId}
												agentId={agentId}
											/>
										</ul>
										{tree.data.truncated && (
											<p className="text-sm text-muted-foreground">
												This tree is truncated. Open a
												child run for more detail.
											</p>
										)}
									</>
								)}
							</section>
							<section className="space-y-2 border-t pt-4">
								<h2 className="text-sm font-semibold">
									Runtime state
								</h2>
								<dl className="space-y-3 text-sm">
									{[
										["Attempt", run.attempt],
										["Checkpoint", run.checkpoint_sequence],
										[
											"Wake",
											run.wake_at
												? new Date(
														run.wake_at,
													).toLocaleString()
												: "No scheduled wake",
										],
										[
											"Lease owner",
											run.lease?.owner ?? "Unclaimed",
										],
										[
											"Lease expires",
											run.lease?.expires_at ??
												"No active lease",
										],
										[
											"Output contract",
											run.contract?.valid === true
												? "Passed"
												: run.contract?.valid === false
													? "Failed"
													: "Not evaluated",
										],
										[
											"Completion event",
											run.completion_event?.emitted_at
												? "Emitted"
												: run.completion_event
															?.pending_at
													? "Pending"
													: "Not emitted",
										],
									].map(([label, value]) => (
										<div key={label}>
											<dt className="text-muted-foreground">
												{label}
											</dt>
											<dd className="break-words">
												{value}
											</dd>
										</div>
									))}
								</dl>
								{run.contract?.errors?.map((error) => (
									<p
										key={error}
										className="text-sm text-destructive"
									>
										{error}
									</p>
								))}
								{run.completion_event?.last_error && (
									<p className="text-sm text-destructive">
										{run.completion_event.last_error}
									</p>
								)}
							</section>
						</aside>
						<div className="min-w-0 space-y-6">
							<section aria-label="Timeline">
								<div className="flex flex-wrap items-end justify-between gap-3">
									<h2 className="text-lg font-semibold">
										Timeline
									</h2>
									<div className="flex flex-wrap gap-3">
										<div>
											<Label htmlFor="timeline-kind">
												Event kind
											</Label>
											<Input
												id="timeline-kind"
												placeholder="All kinds"
												value={kind}
												onChange={(event) =>
													setKind(event.target.value)
												}
											/>
										</div>
										<div className="w-28">
											<Label htmlFor="timeline-attempt">
												Attempt
											</Label>
											<Input
												id="timeline-attempt"
												type="number"
												min={1}
												placeholder="All"
												value={attempt}
												onChange={(event) =>
													setAttempt(
														event.target.value,
													)
												}
											/>
										</div>
									</div>
								</div>
								{sequence && (
									<p className="mt-2 text-sm text-muted-foreground">
										Linked evidence sequence {sequence}.{" "}
										<Link
											className="underline"
											to={`/agents/${agentId}/runs/${runId}/debug`}
										>
											Show full timeline
										</Link>
									</p>
								)}
								<PlatformError
									error={timeline.error}
									retry={() => void timeline.refetch()}
								/>
								{timeline.isPending && (
									<p role="status">Loading timeline…</p>
								)}
								{!timeline.isPending &&
									!timeline.error &&
									!entries.length && (
										<p className="py-8 text-sm text-muted-foreground">
											No durable events match this view.
											Older runs remain available in the
											original run detail.
										</p>
									)}
								<ol className="mt-4 max-h-[65vh] divide-y overflow-auto rounded-md border">
									{entries.map((entry) => (
										<li
											key={`${entry.run_id}:${entry.sequence}`}
											id={`sequence-${entry.sequence}`}
											className={
												entry.sequence === sequence
													? "bg-primary/5 p-4"
													: "p-4"
											}
										>
											<details
												open={
													entry.sequence ===
														sequence || undefined
												}
											>
												<summary className="cursor-pointer text-sm">
													<span className="mr-2 font-mono text-muted-foreground">
														#{entry.sequence}
													</span>
													<span className="font-medium">
														{entry.summary}
													</span>
													<span className="ml-2 text-xs text-muted-foreground">
														{entry.kind} · Attempt{" "}
														{entry.attempt ?? "—"} ·{" "}
														{new Date(
															entry.created_at,
														).toLocaleTimeString()}
													</span>
												</summary>
												<div className="mt-3 space-y-2">
													<Link
														className="text-xs underline"
														to={`?sequence=${entry.sequence}`}
													>
														Link to sequence{" "}
														{entry.sequence}
													</Link>
													<EvidenceJson
														value={entry.detail}
														label={`Sequence ${entry.sequence} detail`}
													/>
													{entry.child_run_id && (
														<Link
															className="text-sm underline"
															to={`/agents/${agentId}/runs/${entry.child_run_id}/debug`}
														>
															Inspect child run
														</Link>
													)}
												</div>
											</details>
										</li>
									))}
								</ol>
								{timeline.hasNextPage && (
									<Button
										className="mt-3"
										variant="outline"
										disabled={timeline.isFetchingNextPage}
										onClick={() =>
											void timeline.fetchNextPage()
										}
									>
										Load more events
									</Button>
								)}
							</section>
							<Card>
								<CardHeader>
									<CardTitle
										role="heading"
										aria-level={2}
										className="text-base"
									>
										Immutable snapshot
									</CardTitle>
								</CardHeader>
								<CardContent className="space-y-3">
									{run.snapshot_version == null ? (
										<p className="text-sm text-muted-foreground">
											This older run has no immutable
											snapshot. Its original history and
											actions remain available.
										</p>
									) : (
										<>
											<p className="text-sm">
												Version {run.snapshot_version} ·{" "}
												{run.model?.provider} /{" "}
												{run.model?.model}
											</p>
											<p className="break-all font-mono text-xs">
												Prompt SHA-256:{" "}
												{run.system_prompt_sha256 ??
													"Not recorded"}
											</p>
											<details>
												<summary className="cursor-pointer text-sm">
													Limits, tools, delegates and
													correlation
												</summary>
												<EvidenceJson
													label="Snapshot configuration"
													value={{
														limits: run.limits,
														tools: run.tool_names,
														system_tools:
															run.system_tools,
														delegated_agents:
															run.delegated_agents,
														correlation:
															run.correlation,
													}}
												/>
											</details>
										</>
									)}
								</CardContent>
							</Card>
							<PlatformError
								error={detail.error}
								retry={() => void detail.refetch()}
							/>
							{detail.data && (
								<RunAIUsageCard
									usage={detail.data.ai_usage ?? []}
									totals={detail.data.ai_totals ?? null}
									reported={{
										model: detail.data.llm_model ?? null,
										tokens: detail.data.tokens_used,
									}}
								/>
							)}
							<section>
								<h2 className="mb-3 text-lg font-semibold">
									Checkpoints
								</h2>
								<PlatformError
									error={checkpoints.error}
									retry={() => void checkpoints.refetch()}
								/>
								{checkpoints.isPending && (
									<p role="status">Loading checkpoints…</p>
								)}
								{checkpoints.data?.pages.every(
									(page) => !page.checkpoints?.length,
								) && (
									<p className="text-sm text-muted-foreground">
										No durable checkpoints recorded.
									</p>
								)}
								<ul className="max-h-72 divide-y overflow-auto">
									{checkpoints.data?.pages
										.flatMap(
											(page) => page.checkpoints ?? [],
										)
										.map((point) => (
											<li
												key={point.sequence}
												className="py-3 text-sm"
											>
												<span className="font-medium">
													Checkpoint {point.sequence}
												</span>{" "}
												· Attempt {point.attempt} ·{" "}
												{point.message_count ??
													"Unknown"}{" "}
												messages
												<p className="text-muted-foreground">
													{point.has_pending_tool_calls
														? "Waiting for tool completion. "
														: ""}
													{point.has_pending_join
														? "Waiting for delegated runs. "
														: ""}
													{point.has_pending_timer
														? "Waiting for scheduled wake. "
														: ""}
													{new Date(
														point.created_at,
													).toLocaleString()}
												</p>
											</li>
										))}
								</ul>
								{checkpoints.hasNextPage && (
									<Button
										variant="outline"
										onClick={() =>
											void checkpoints.fetchNextPage()
										}
										disabled={
											checkpoints.isFetchingNextPage
										}
									>
										Load more checkpoints
									</Button>
								)}
							</section>
						</div>
					</div>
				</>
			)}
		</div>
	);
}
