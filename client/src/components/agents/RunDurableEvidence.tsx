import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { agentPlatform } from "@/services/agentPlatform";
import { useAgentPlatformUpdates } from "@/hooks/useAgentPlatformUpdates";
import {
	EvidenceJson,
	PlatformError,
	PlatformStatus,
} from "@/components/agents/evaluation/PlatformEvidence";
import type { components } from "@/lib/v1";

/**
 * RunDurableEvidence — durable execution evidence rendered inside the run
 * detail Advanced view (one investigation view; no standalone debugger).
 *
 * Sections render only when relevant: empty infrastructure fields stay
 * hidden, and older runs without an immutable snapshot keep a clear
 * legacy note instead. Waiting pauses read "Waiting until [time]" with
 * the timer reason taken from the journaled timer entry.
 */
function RunBranch({
	node,
	agentId,
}: {
	node: components["schemas"]["AgentRunTreeNode"];
	agentId: string;
}) {
	return (
		<li className="space-y-2">
			<Link
				className="flex flex-wrap items-center gap-2 rounded-md p-2 text-sm hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
				to={`/agents/${node.agent_id ?? agentId}/runs/${node.run_id}`}
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
							agentId={agentId}
						/>
					))}
				</ul>
			)}
		</li>
	);
}

export function RunDurableEvidence({
	runId,
	agentId,
	highlightSequence,
}: {
	runId: string;
	agentId: string;
	highlightSequence?: number;
}) {
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
			highlightSequence,
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
	const run = snapshot.data;
	const entries =
		timeline.data?.pages.flatMap((page) => page.entries ?? []) ?? [];
	const { hasNextPage, isFetching, isError, fetchNextPage } = timeline;
	const targetFound =
		highlightSequence == null ||
		entries.some((entry) => entry.sequence === highlightSequence);
	useEffect(() => {
		if (
			highlightSequence != null &&
			!targetFound &&
			hasNextPage &&
			!isFetching &&
			!isError
		)
			void fetchNextPage();
	}, [
		highlightSequence,
		targetFound,
		hasNextPage,
		isFetching,
		isError,
		fetchNextPage,
	]);
	useEffect(() => {
		if (highlightSequence != null && targetFound)
			document
				.getElementById(`sequence-${highlightSequence}`)
				?.scrollIntoView({ block: "center" });
	}, [highlightSequence, targetFound]);
	const timerReason = [...entries]
		.reverse()
		.find((entry) => entry.kind === "timer")?.detail?.reason;
	const showTree =
		tree.isPending ||
		tree.isError ||
		(tree.data?.total_runs ?? 0) > 1 ||
		(tree.data?.truncated ?? false);
	const checkpointList =
		checkpoints.data?.pages.flatMap((page) => page.checkpoints ?? []) ?? [];
	return (
		<div className="space-y-6">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<h3 className="text-sm font-semibold">Durable evidence</h3>
				<Button
					variant="outline"
					className="min-h-11"
					onClick={() => {
						void snapshot.refetch();
						void timeline.refetch();
						void checkpoints.refetch();
						void tree.refetch();
					}}
				>
					Refresh evidence
				</Button>
			</div>
			<PlatformError
				error={snapshot.error}
				retry={() => void snapshot.refetch()}
			/>
			{snapshot.isPending && <p role="status">Loading run snapshot…</p>}
			{run && (
				<>
					{showTree && (
						<section aria-label="Run tree">
							<h4 className="mb-2 text-sm font-semibold">
								Delegated runs
							</h4>
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
					)}
					<section aria-label="Runtime state">
						<h4 className="mb-2 text-sm font-semibold">
							Runtime state
						</h4>
						{run.wake_at && (
							<p role="status" className="mb-2 text-sm">
								Waiting until{" "}
								{new Date(run.wake_at).toLocaleString()}
								{typeof timerReason === "string" &&
								timerReason ? (
									<> — {timerReason}</>
								) : null}
							</p>
						)}
						<dl className="space-y-3 text-sm">
							<div>
								<dt className="text-muted-foreground">
									Attempt
								</dt>
								<dd>{run.attempt}</dd>
							</div>
							<div>
								<dt className="text-muted-foreground">
									Checkpoint
								</dt>
								<dd>{run.checkpoint_sequence}</dd>
							</div>
							{run.lease?.owner && (
								<div>
									<dt className="text-muted-foreground">
										Lease owner
									</dt>
									<dd className="break-words">
										{run.lease.owner}
									</dd>
								</div>
							)}
							{run.lease?.expires_at && (
								<div>
									<dt className="text-muted-foreground">
										Lease expires
									</dt>
									<dd className="break-words">
										{run.lease.expires_at}
									</dd>
								</div>
							)}
							{(run.contract?.valid != null ||
								!!run.contract?.errors?.length) && (
								<div>
									<dt className="text-muted-foreground">
										Output contract
									</dt>
									<dd>
										{run.contract?.valid === true
											? "Passed"
											: run.contract?.valid === false
												? "Failed"
												: "Not evaluated"}
									</dd>
								</div>
							)}
							{(run.completion_event?.emitted_at ||
								run.completion_event?.pending_at ||
								run.completion_event?.last_error) && (
								<div>
									<dt className="text-muted-foreground">
										Completion event
									</dt>
									<dd>
										{run.completion_event?.emitted_at
											? "Emitted"
											: run.completion_event?.pending_at
												? "Pending"
												: "Not emitted"}
									</dd>
								</div>
							)}
						</dl>
						{run.contract?.errors?.map((error) => (
							<p key={error} className="text-sm text-destructive">
								{error}
							</p>
						))}
						{run.completion_event?.last_error && (
							<p className="text-sm text-destructive">
								{run.completion_event.last_error}
							</p>
						)}
					</section>
					<section aria-label="Journal timeline">
						<div className="flex flex-wrap items-end justify-between gap-3">
							<h4 className="text-sm font-semibold">
								Journal timeline
							</h4>
						</div>
						<div className="mt-3">
							<ListToolbar>
								<div className="min-w-0 flex-1 sm:max-w-xs">
									<Label htmlFor="evidence-timeline-kind">
										Event kind
									</Label>
									<Input
										id="evidence-timeline-kind"
										placeholder="All kinds"
										value={kind}
										onChange={(event) =>
											setKind(event.target.value)
										}
									/>
								</div>
								<div className="w-28">
									<Label htmlFor="evidence-timeline-attempt">
										Attempt
									</Label>
									<Input
										id="evidence-timeline-attempt"
										type="number"
										min={1}
										placeholder="All"
										value={attempt}
										onChange={(event) =>
											setAttempt(event.target.value)
										}
									/>
								</div>
							</ListToolbar>
						</div>
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
									No durable events recorded for this run
									yet.
								</p>
							)}
						<ol className="mt-4 max-h-[65vh] divide-y overflow-auto rounded-md border">
							{entries.map((entry) => (
								<li
									key={`${entry.run_id}:${entry.sequence}`}
									id={`sequence-${entry.sequence}`}
									className={
										entry.sequence === highlightSequence
											? "bg-primary/5 p-4"
											: "p-4"
									}
								>
									<details
										open={
											entry.sequence ===
												highlightSequence || undefined
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
												to={`?tab=activity&sequence=${entry.sequence}`}
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
													to={`/agents/${agentId}/runs/${entry.child_run_id}`}
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
								onClick={() => void timeline.fetchNextPage()}
							>
								Load more events
							</Button>
						)}
					</section>
					<section aria-label="Saved run snapshot">
						<h4 className="mb-2 text-sm font-semibold">
							Saved run snapshot
						</h4>
						{run.snapshot_version == null ? (
							<p className="text-sm text-muted-foreground">
								This older run has no saved snapshot. Its
								original history and actions remain available.
							</p>
						) : (
							<>
								<p className="text-sm">
									Version {run.snapshot_version} ·{" "}
									{run.model?.provider} / {run.model?.model}
								</p>
								<p className="break-all font-mono text-xs">
									Prompt SHA-256:{" "}
									{run.system_prompt_sha256 ?? "Not recorded"}
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
											system_tools: run.system_tools,
											delegated_agents:
												run.delegated_agents,
											correlation: run.correlation,
										}}
									/>
								</details>
							</>
						)}
					</section>
					<section aria-label="Checkpoints">
						<h4 className="mb-2 text-sm font-semibold">
							Checkpoints
						</h4>
						<PlatformError
							error={checkpoints.error}
							retry={() => void checkpoints.refetch()}
						/>
						{checkpoints.isPending && (
							<p role="status">Loading checkpoints…</p>
						)}
						{!checkpoints.isPending &&
							!checkpoints.error &&
							!checkpointList.length && (
								<p className="text-sm text-muted-foreground">
									No durable checkpoints recorded.
								</p>
							)}
						{!!checkpointList.length && (
							<ul className="max-h-72 divide-y overflow-auto">
								{checkpointList.map((point) => (
									<li
										key={point.sequence}
										className="py-3 text-sm"
									>
										<span className="font-medium">
											Checkpoint {point.sequence}
										</span>{" "}
										· Attempt {point.attempt} ·{" "}
										{point.message_count ?? "Unknown"}{" "}
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
						)}
						{checkpoints.hasNextPage && (
							<Button
								variant="outline"
								onClick={() =>
									void checkpoints.fetchNextPage()
								}
								disabled={checkpoints.isFetchingNextPage}
							>
								Load more checkpoints
							</Button>
						)}
					</section>
				</>
			)}
		</div>
	);
}
