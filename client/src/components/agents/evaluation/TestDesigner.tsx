import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useInfiniteAgentRuns } from "@/services/agentRuns";
import { agentPlatform } from "@/services/agentPlatform";
import { useAgentPlatformUpdates } from "@/hooks/useAgentPlatformUpdates";
import {
	EvidenceJson,
	PlatformError,
	PlatformStatus,
} from "./PlatformEvidence";

export function TestDesigner({
	suiteId,
	agentId,
	runId,
	onQueued,
}: {
	suiteId: string;
	agentId: string;
	runId?: string;
	onQueued: (id: string) => void;
}) {
	const [goal, setGoal] = useState("");
	const [count, setCount] = useState(4);
	const [selected, setSelected] = useState<string[]>([]);
	const [search, setSearch] = useState("");
	const history = useInfiniteAgentRuns({
		agentId,
		q: search || undefined,
		pageSize: 10,
	});
	useAgentPlatformUpdates(runId);
	const snapshot = useQuery({
		queryKey: ["agent-platform", runId, "snapshot"],
		queryFn: () => agentPlatform.snapshot(runId!),
		enabled: !!runId,
	});
	const queue = useMutation({
		mutationFn: () =>
			agentPlatform.designer(suiteId, {
				suite_goal: goal,
				requested_count: count,
				historical_run_ids: selected,
			}),
		onSuccess: (data) => onQueued(data.run_id),
	});
	const materialized =
		snapshot.data?.correlation?.designer_materialized === true;
	const materializationError = snapshot.data?.correlation?.designer_error;
	const terminalFailure = [
		"failed",
		"contract_failed",
		"cancelled",
		"timeout",
		"budget_exceeded",
		"recovery_required",
	].includes(snapshot.data?.status ?? "");
	const pending = !!runId && !materialized && !terminalFailure;
	return (
		<section aria-label="Test Designer" className="space-y-4">
			<div>
				<h3 className="text-lg font-semibold">Test Designer</h3>
				<p className="mt-1 max-w-prose text-sm text-muted-foreground">
					Generate coherent synthetic cases using the dedicated
					Testing model. Review selected previous runs for realistic
					tool arguments, response shapes and failures. Generated
					cases stay disabled until you accept them.
				</p>
			</div>
			<form
				className="space-y-4"
				onSubmit={(event) => {
					event.preventDefault();
					queue.mutate();
				}}
			>
				<div>
					<Label htmlFor="designer-goal">
						What should these cases test?
					</Label>
					<Textarea
						id="designer-goal"
						required
						rows={3}
						value={goal}
						onChange={(event) => setGoal(event.target.value)}
						placeholder="Include successful operations, empty responses, partial data and recoverable tool failures…"
					/>
				</div>
				<div className="max-w-40">
					<Label htmlFor="designer-count">Draft count</Label>
					<Input
						id="designer-count"
						type="number"
						min={1}
						max={10}
						value={count}
						onChange={(event) =>
							setCount(Number(event.target.value))
						}
					/>
				</div>
				<details>
					<summary className="cursor-pointer text-sm font-medium">
						Review historical runs · {selected.length} selected
					</summary>
					<div className="mt-3 space-y-3">
						<Label htmlFor="history-search">
							Search previous runs
						</Label>
						<Input
							id="history-search"
							value={search}
							onChange={(event) => setSearch(event.target.value)}
							placeholder="Search run summaries"
						/>
						<p className="text-xs text-muted-foreground">
							Select up to 20 authorized runs. The Designer
							receives bounded, redacted evidence; these runs are
							inspiration, never replayed against real tools.
						</p>
						<PlatformError
							error={history.error}
							retry={() => void history.refetch()}
						/>
						{history.isPending && (
							<p role="status">Loading previous runs…</p>
						)}
						<ul className="max-h-80 divide-y overflow-auto">
							{history.data?.pages
								.flatMap((page) => page.items)
								.map((run) => (
									<li key={run.id} className="space-y-2 py-3">
										<label className="flex items-center gap-3 text-sm">
											<input
												type="checkbox"
												checked={selected.includes(
													run.id,
												)}
												disabled={
													!selected.includes(
														run.id,
													) && selected.length >= 20
												}
												onChange={(event) =>
													setSelected((ids) =>
														event.target.checked
															? [...ids, run.id]
															: ids.filter(
																	(id) =>
																		id !==
																		run.id,
																),
													)
												}
											/>
											<span>
												Select run {run.id.slice(0, 8)}{" "}
												·{" "}
												{new Date(
													run.created_at,
												).toLocaleString()}
											</span>
											<PlatformStatus
												status={run.status}
											/>
										</label>
										<details className="ml-7 text-sm">
											<summary className="cursor-pointer">
												Review invocation and outcome
											</summary>
											<EvidenceJson
												label={`Historical run ${run.id}`}
												value={{
													input: run.input,
													output: run.output,
													error: run.error,
												}}
											/>
											<Link
												className="underline"
												target="_blank"
												rel="noreferrer"
												to={`/agents/${agentId}/runs/${run.id}/debug`}
											>
												Review tool arguments and
												results in debugger (new tab)
											</Link>
										</details>
									</li>
								))}
						</ul>
						{history.data?.pages[0]?.total === 0 && (
							<p className="text-sm text-muted-foreground">
								No previous runs match. You can generate cases
								from the Agent configuration alone.
							</p>
						)}
						{history.hasNextPage && (
							<Button
								type="button"
								variant="outline"
								onClick={() => void history.fetchNextPage()}
								disabled={history.isFetchingNextPage}
							>
								Load more previous runs
							</Button>
						)}
					</div>
				</details>
				<PlatformError error={queue.error} />
				<Button disabled={queue.isPending || pending}>
					{queue.isPending
						? "Queuing…"
						: pending
							? "Designer in progress"
							: "Generate review drafts"}
				</Button>
			</form>
			{runId && (
				<div role="status" className="space-y-2 rounded-md border p-4">
					<div className="flex flex-wrap items-center gap-2">
						<PlatformStatus
							status={
								materializationError
									? "failed"
									: materialized
										? "completed"
										: snapshot.data?.status === "completed"
											? "waiting"
											: (snapshot.data?.status ??
												"queued")
							}
						/>
						<span className="text-sm">
							{materializationError
								? String(materializationError)
								: materialized
									? "Draft generation finished. Review the cases below before accepting."
									: snapshot.data?.status === "completed"
										? "Model completed. Preparing review drafts…"
										: terminalFailure
											? "Test Designer stopped before drafts were ready. Inspect the run, then generate corrected drafts."
											: "Test Designer is generating drafts."}
						</span>
					</div>
					<Link
						className="text-sm underline"
						to={`/agents/${agentId}/runs/${runId}/debug`}
					>
						Inspect Designer run
					</Link>
					<PlatformError
						error={snapshot.error}
						retry={() => void snapshot.refetch()}
					/>
				</div>
			)}
		</section>
	);
}
