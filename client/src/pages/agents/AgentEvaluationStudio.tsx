import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
	useInfiniteQuery,
	useMutation,
	useQuery,
	useQueryClient,
} from "@tanstack/react-query";
import { ArrowLeft, FlaskConical } from "lucide-react";
import { toast } from "sonner";
import { useAgent } from "@/hooks/useAgents";
import { useAgentPlatformUpdates } from "@/hooks/useAgentPlatformUpdates";
import { agentPlatform } from "@/services/agentPlatform";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SuiteEditor } from "@/components/agents/evaluation/SuiteEditor";
import { CaseEditor } from "@/components/agents/evaluation/CaseEditor";
import {
	CandidateEditor,
	CandidatePromotion,
} from "@/components/agents/evaluation/CandidateEditor";
import { TestDesigner } from "@/components/agents/evaluation/TestDesigner";
import { ExecutionResults } from "@/components/agents/evaluation/ExecutionResults";
import {
	EvidenceJson,
	PlatformError,
	PlatformStatus,
} from "@/components/agents/evaluation/PlatformEvidence";
import type { components } from "@/lib/v1";

export function AgentEvaluationStudio() {
	const { id = "" } = useParams();
	const agent = useAgent(id);
	const [params, setParams] = useSearchParams();
	const suiteId = params.get("suite") ?? "";
	const candidateId = params.get("candidate") ?? "";
	const executionId = params.get("execution") ?? "";
	const designerId = params.get("designer") ?? undefined;
	const client = useQueryClient();
	const [createOpen, setCreateOpen] = useState(false);
	const [suiteName, setSuiteName] = useState("");
	const [suiteDescription, setSuiteDescription] = useState("");
	const [editing, setEditing] = useState<
		components["schemas"]["EvaluationCasePublic"] | "new" | null
	>(null);
	const [confirmPublish, setConfirmPublish] = useState(false);
	function navigate(values: Record<string, string | undefined>) {
		const next = new URLSearchParams(params);
		Object.entries(values).forEach(([key, value]) => {
			if (value) next.set(key, value);
			else next.delete(key);
		});
		setParams(next);
	}
	const refresh = () => {
		void client.invalidateQueries({ queryKey: ["agent-platform"] });
	};
	const suites = useInfiniteQuery({
		queryKey: ["agent-platform", "suites"],
		queryFn: ({ pageParam }) => agentPlatform.suites(pageParam),
		initialPageParam: 0,
		getNextPageParam: (last, pages) =>
			last.length === 50 ? pages.length * 50 : undefined,
	});
	const suite = useQuery({
		queryKey: ["agent-platform", "suite", suiteId],
		queryFn: () => agentPlatform.suite(suiteId),
		enabled: !!suiteId,
	});
	const cases = useQuery({
		queryKey: ["agent-platform", "cases", suiteId],
		queryFn: () => agentPlatform.cases(suiteId),
		enabled: !!suiteId,
	});
	const candidate = useQuery({
		queryKey: ["agent-platform", "candidate", candidateId],
		queryFn: () => agentPlatform.candidate(candidateId),
		enabled: !!candidateId,
	});
	const execution = useQuery({
		queryKey: ["agent-platform", "execution", executionId],
		queryFn: () => agentPlatform.execution(executionId),
		enabled: !!executionId,
	});
	const results = useQuery({
		queryKey: ["agent-platform", "results", executionId],
		queryFn: () => agentPlatform.results(executionId),
		enabled: !!executionId,
	});
	useAgentPlatformUpdates(
		designerId,
		execution.data?.platform_job_id ?? undefined,
	);
	const create = useMutation({
		mutationFn: () =>
			agentPlatform.createSuite({
				name: suiteName,
				description: suiteDescription,
				agent_id: id,
				organization_id: agent.data?.organization_id,
			}),
		onSuccess: (data) => {
			refresh();
			navigate({
				suite: data.id,
				execution: undefined,
				designer: undefined,
			});
			setCreateOpen(false);
			setSuiteName("");
			setSuiteDescription("");
		},
	});
	const publish = useMutation({
		mutationFn: () => agentPlatform.publish(suiteId),
		onSuccess: () => {
			refresh();
			setConfirmPublish(false);
		},
	});
	const accept = useMutation({
		mutationFn: (draftId: string) => agentPlatform.accept(suiteId, draftId),
		onSuccess: refresh,
	});
	const run = useMutation({
		mutationFn: () =>
			agentPlatform.execute({
				suite_id: suiteId,
				candidate_id: candidateId || null,
			}),
		onSuccess: (data) => {
			navigate({ execution: data.executionId, tab: "results" });
			refresh();
		},
	});
	const cancel = useMutation({
		mutationFn: () => agentPlatform.cancel(executionId),
		onSuccess: refresh,
	});
	const tab = params.get("tab") ?? "cases";
	const frozen = suite.data?.status === "published";
	const active = ["queued", "running", "waiting"].includes(
		execution.data?.status ?? "",
	);
	const mismatch =
		(suite.data && suite.data.agent_id !== id) ||
		(candidate.data && candidate.data.base_agent_id !== id) ||
		(execution.data &&
			(execution.data.suite_id !== suiteId ||
				(execution.data.candidate_id ?? "") !== candidateId));
	return (
		<div className="mx-auto w-full max-w-6xl space-y-6 pb-10">
			<Link
				className="inline-flex min-h-11 items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
				to={`/agents/${id}`}
			>
				<ArrowLeft aria-hidden="true" className="size-4" />
				Back to {agent.data?.name ?? "Agent"}
			</Link>
			<header className="flex flex-wrap items-start justify-between gap-4">
				<div>
					<h1 className="font-display text-2xl font-semibold">
						Evaluation Studio
					</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						Test {agent.data?.name ?? "Agent"} with frozen synthetic
						cases before changing production.
					</p>
				</div>
				<Badge variant="outline">
					<FlaskConical aria-hidden="true" className="size-3" />
					Synthetic tools only
				</Badge>
			</header>
			<PlatformError
				error={agent.error}
				retry={() => void agent.refetch()}
			/>
			{agent.isPending && <p role="status">Loading Agent…</p>}
			<section className="rounded-lg border p-4">
				<div className="flex flex-wrap items-start justify-between gap-4">
					<div>
						<h2 className="text-sm font-semibold">
							Baseline · live Agent
						</h2>
						<p className="mt-1 text-sm">
							{agent.data?.name ?? "Loading…"}
						</p>
						<p className="mt-1 text-xs text-muted-foreground">
							A fresh baseline snapshot is frozen for each
							execution.
						</p>
					</div>
					<div>
						<h2 className="text-sm font-semibold">
							Candidate · evaluation only
						</h2>
						<p className="mt-1 text-sm">
							{candidate.data?.name ??
								(candidateId
									? "Loading candidate…"
									: "Baseline only")}
						</p>
						{candidateId && (
							<Button
								size="sm"
								variant="ghost"
								onClick={() =>
									navigate({
										candidate: undefined,
										execution: undefined,
									})
								}
							>
								Use baseline only
							</Button>
						)}
					</div>
				</div>
			</section>
			<PlatformError
				error={candidate.error}
				retry={() => void candidate.refetch()}
			/>
			<section className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
				<div>
					<Label htmlFor="studio-suite">Suite</Label>
					<select
						id="studio-suite"
						className="h-10 w-full rounded-md border bg-background px-3 text-sm"
						value={suiteId}
						onChange={(event) => {
							navigate({
								suite: event.target.value,
								execution: undefined,
								designer: undefined,
							});
							setEditing(null);
							setConfirmPublish(false);
						}}
					>
						<option value="">Choose a suite</option>
						{suites.data?.pages
							.flat()
							.filter((item) => item.agent_id === id)
							.map((item) => (
								<option value={item.id} key={item.id}>
									{item.name} · {item.status} · v
									{item.version}
								</option>
							))}
					</select>
					<PlatformError
						error={suites.error}
						retry={() => void suites.refetch()}
					/>
					{suites.isPending && <p role="status">Loading suites…</p>}
					{suites.hasNextPage && (
						<Button
							variant="outline"
							disabled={suites.isFetchingNextPage}
							onClick={() => void suites.fetchNextPage()}
						>
							{suites.isFetchingNextPage
								? "Loading…"
								: "Load more suites"}
						</Button>
					)}
				</div>
				<details
					open={createOpen}
					onToggle={(event) =>
						setCreateOpen(event.currentTarget.open)
					}
				>
					<summary className="cursor-pointer py-2 text-sm font-medium">
						Create a suite
					</summary>
					<form
						className="mt-2 space-y-3"
						onSubmit={(event) => {
							event.preventDefault();
							create.mutate();
						}}
					>
						<Label htmlFor="suite-name">Suite name</Label>
						<Input
							id="suite-name"
							required
							value={suiteName}
							onChange={(event) =>
								setSuiteName(event.target.value)
							}
						/>
						<Label htmlFor="suite-description">Description</Label>
						<Input
							id="suite-description"
							value={suiteDescription}
							onChange={(event) =>
								setSuiteDescription(event.target.value)
							}
						/>
						<PlatformError error={create.error} />
						<Button disabled={create.isPending || !agent.data}>
							{create.isPending ? "Creating…" : "Create suite"}
						</Button>
					</form>
				</details>
			</section>
			{mismatch ? (
				<p role="alert">
					This suite, candidate or execution belongs to a different
					Agent context. Choose the matching suite and candidate
					before continuing.
				</p>
			) : (
				<>
					<PlatformError
						error={suite.error}
						retry={() => void suite.refetch()}
					/>
					{!suiteId && (
						<div className="rounded-lg border border-dashed p-8 text-center">
							<h2 className="text-lg font-semibold">
								Start with a suite
							</h2>
							<p className="mx-auto mt-2 max-w-prose text-sm text-muted-foreground">
								Group the situations this Agent should handle.
								Author a case or ask Test Designer for drafts,
								review the responses and assertions, then freeze
								the suite for repeatable comparisons.
							</p>
						</div>
					)}
					{suite.data && (
						<>
							<div className="flex flex-wrap items-center justify-between gap-3">
								<div>
									<h2 className="text-xl font-semibold">
										{suite.data.name}
									</h2>
									<p className="text-sm text-muted-foreground">
										{suite.data.description} · Version{" "}
										{suite.data.version} ·{" "}
										{frozen
											? "Published · frozen fixtures"
											: "Draft suite"}
									</p>
								</div>
								<div className="flex flex-wrap gap-2">
									{!frozen && (
										<Button
											variant="outline"
											disabled={
												!cases.data?.some(
													(item) =>
														item.accepted &&
														item.enabled,
												)
											}
											onClick={() =>
												setConfirmPublish(true)
											}
										>
											Publish frozen suite
										</Button>
									)}
									<Button
										disabled={
											!frozen ||
											run.isPending ||
											active ||
											(!!candidateId && !candidate.data)
										}
										onClick={() => run.mutate()}
									>
										{run.isPending
											? "Queuing…"
											: candidateId
												? "Run baseline and candidate"
												: "Run baseline"}
									</Button>
								</div>
							</div>
							{!frozen && (
								<SuiteEditor
									key={`${suiteId}:${suite.data.version}`}
									suite={suite.data}
									onSaved={refresh}
								/>
							)}
							<PlatformError error={run.error} />
							{confirmPublish && (
								<section className="space-y-3 rounded-md border p-4">
									<h3 className="font-semibold">
										Freeze this suite for execution?
									</h3>
									<p className="text-sm text-muted-foreground">
										Publishing locks the suite and its case
										versions. Only accepted, enabled cases
										execute. Review or accept any remaining
										drafts before publishing.
									</p>
									<PlatformError error={publish.error} />
									<div className="flex gap-2">
										<Button
											disabled={publish.isPending}
											onClick={() => publish.mutate()}
										>
											Confirm publish suite
										</Button>
										<Button
											variant="outline"
											onClick={() =>
												setConfirmPublish(false)
											}
										>
											Keep editing
										</Button>
									</div>
								</section>
							)}
							<Tabs
								value={tab}
								onValueChange={(value) =>
									navigate({ tab: value })
								}
							>
								<TabsList className="flex h-auto w-fit max-w-full flex-wrap">
									<TabsTrigger value="cases">
										Cases
									</TabsTrigger>
									<TabsTrigger value="candidate">
										Candidate
									</TabsTrigger>
									<TabsTrigger value="results">
										Results
									</TabsTrigger>
								</TabsList>
								<TabsContent
									value="cases"
									className="space-y-6 pt-4"
								>
									{!frozen && (
										<TestDesigner
											key={suiteId}
											suiteId={suiteId}
											agentId={id}
											runId={designerId}
											onQueued={(runId) =>
												navigate({ designer: runId })
											}
										/>
									)}
									<section className="space-y-4 border-t pt-5">
										<div className="flex flex-wrap items-center justify-between gap-3">
											<h3 className="text-lg font-semibold">
												Cases and review drafts
											</h3>
											{!frozen && (
												<Button
													variant="outline"
													onClick={() =>
														setEditing("new")
													}
												>
													Author case
												</Button>
											)}
										</div>
										<PlatformError
											error={cases.error}
											retry={() => void cases.refetch()}
										/>
										<PlatformError error={accept.error} />
										{cases.isPending && (
											<p role="status">Loading cases…</p>
										)}
										{editing && (
											<CaseEditor
												key={
													editing === "new"
														? "new"
														: editing.id
												}
												suiteId={suiteId}
												draft={
													editing === "new"
														? undefined
														: editing
												}
												onSaved={() => {
													setEditing(null);
													refresh();
												}}
												onCancel={() =>
													setEditing(null)
												}
											/>
										)}
										{cases.data?.length === 0 &&
											!editing && (
												<p className="py-6 text-sm text-muted-foreground">
													No cases yet. Generate
													review drafts or author the
													first regression case.
												</p>
											)}
										<ul className="divide-y rounded-lg border">
											{cases.data?.map((item) => (
												<li
													key={item.id}
													className="space-y-3 p-4"
												>
													<div className="flex flex-wrap items-center justify-between gap-3">
														<div>
															<h4 className="font-medium">
																{item.name}{" "}
																<span className="font-normal text-muted-foreground">
																	v
																	{
																		item.version
																	}
																</span>
															</h4>
															<p className="mt-1 text-xs text-muted-foreground">
																{item.accepted
																	? "Accepted · frozen"
																	: "Draft · review required · disabled"}{" "}
																·{" "}
																{
																	item.provenance
																}{" "}
																·{" "}
																{
																	item.repetitions
																}{" "}
																repetition(s)
															</p>
														</div>
														{!item.accepted &&
															!frozen && (
																<Button
																	variant="outline"
																	onClick={() =>
																		setEditing(
																			item,
																		)
																	}
																>
																	Edit draft
																</Button>
															)}
													</div>
													<details>
														<summary className="cursor-pointer text-sm font-medium">
															Inspect input, mock
															fixture and
															assertions
														</summary>
														<div className="mt-3 space-y-3">
															<EvidenceJson
																label={`${item.name} input`}
																value={
																	item.input
																}
															/>
															<EvidenceJson
																label={`${item.name} fixture`}
																value={
																	item.fixture
																}
															/>
															<EvidenceJson
																label={`${item.name} assertions`}
																value={
																	item.assertions
																}
															/>
															<EvidenceJson
																label={`${item.name} policies`}
																value={{
																	simulator_policy:
																		item.simulator_policy,
																	expected_tools:
																		item.expected_tools,
																	forbidden_tools:
																		item.forbidden_tools,
																	output_schema:
																		item.output_schema,
																	scoring_policy:
																		item.scoring_policy,
																}}
															/>
															{!!item
																.provenance_run_ids
																?.length && (
																<p className="text-xs">
																	Inspired by{" "}
																	{item.provenance_run_ids.map(
																		(
																			runId,
																		) => (
																			<Link
																				key={
																					runId
																				}
																				className="mr-2 underline"
																				to={`/agents/${id}/runs/${runId}/debug`}
																			>
																				{runId.slice(
																					0,
																					8,
																				)}
																			</Link>
																		),
																	)}
																</p>
															)}
															{!item.accepted &&
																!frozen && (
																	<div className="space-y-2">
																		<p className="text-sm text-muted-foreground">
																			Accepting
																			creates
																			a
																			new
																			frozen
																			version.
																			Future
																			regressions
																			reuse
																			this
																			exact
																			fixture.
																		</p>
																		<Button
																			disabled={
																				accept.isPending
																			}
																			onClick={() =>
																				accept.mutate(
																					item.id,
																				)
																			}
																		>
																			Accept
																			and
																			freeze{" "}
																			{
																				item.name
																			}
																		</Button>
																	</div>
																)}
														</div>
													</details>
												</li>
											))}
										</ul>
									</section>
								</TabsContent>
								<TabsContent
									value="candidate"
									className="space-y-6 pt-4"
								>
									{candidate.data ? (
										<>
											<h3 className="text-lg font-semibold">
												{candidate.data.name ??
													"Immutable candidate"}
											</h3>
											<p className="break-all font-mono text-xs text-muted-foreground">
												Snapshot SHA-256:{" "}
												{candidate.data.snapshot_hash}
											</p>
											<EvidenceJson
												label="Frozen candidate overrides"
												value={Object.fromEntries(
													Object.entries(
														candidate.data
															.overlays ?? {},
													).filter(
														([, value]) =>
															value != null,
													),
												)}
											/>
											<details>
												<summary className="cursor-pointer text-sm">
													Effective frozen
													configuration
												</summary>
												<EvidenceJson
													label="Effective candidate snapshot"
													value={
														candidate.data.snapshot
													}
												/>
											</details>
											<Button
												variant="outline"
												onClick={() =>
													navigate({
														candidate: undefined,
														execution: undefined,
													})
												}
											>
												Create another candidate
											</Button>
											<CandidatePromotion
												candidate={candidate.data}
												agentId={id}
												onApplied={() => {
													void agent.refetch();
													toast.success(
														"Candidate changes applied to live Agent",
													);
												}}
											/>
										</>
									) : (
										agent.data && (
											<CandidateEditor
												key={agent.data.updated_at}
												agent={agent.data}
												onCreated={(data) =>
													navigate({
														candidate: data.id,
														execution: undefined,
													})
												}
											/>
										)
									)}
								</TabsContent>
								<TabsContent
									value="results"
									className="space-y-5 pt-4"
								>
									{!executionId ? (
										<p className="py-8 text-sm text-muted-foreground">
											Publish a suite, then run the
											baseline alone or compare it with an
											immutable candidate.
										</p>
									) : (
										<>
											<PlatformError
												error={execution.error}
												retry={() =>
													void execution.refetch()
												}
											/>
											{execution.isPending && (
												<p role="status">
													Loading execution…
												</p>
											)}
											{execution.data && (
												<section className="space-y-3">
													<div className="flex flex-wrap items-center justify-between gap-3">
														<div className="flex flex-wrap items-center gap-3">
															<PlatformStatus
																status={
																	execution
																		.data
																		.status
																}
															/>
															<span className="text-sm">
																{
																	execution
																		.data
																		.completed_cases
																}{" "}
																/{" "}
																{
																	execution
																		.data
																		.total_cases
																}{" "}
																completed ·{" "}
																{
																	execution
																		.data
																		.passed_cases
																}{" "}
																passed ·{" "}
																{
																	execution
																		.data
																		.failed_cases
																}{" "}
																failed
															</span>
														</div>
														{active && (
															<Button
																variant="outline"
																disabled={
																	cancel.isPending
																}
																onClick={() =>
																	cancel.mutate()
																}
															>
																Cancel execution
															</Button>
														)}
													</div>
													<p className="break-all text-xs text-muted-foreground">
														Execution {executionId}{" "}
														· Suite v
														{
															execution.data
																.suite_version
														}
													</p>
													{execution.data.error && (
														<p
															role="alert"
															className="text-sm text-destructive"
														>
															{
																execution.data
																	.error
															}
														</p>
													)}
													<PlatformError
														error={cancel.error}
													/>
												</section>
											)}
											<PlatformError
												error={results.error}
												retry={() =>
													void results.refetch()
												}
											/>
											{results.isPending ? (
												<p role="status">
													Loading evidence…
												</p>
											) : (
												<ExecutionResults
													results={results.data ?? []}
													cases={cases.data ?? []}
													agentId={id}
												/>
											)}
										</>
									)}
								</TabsContent>
							</Tabs>
						</>
					)}
				</>
			)}
		</div>
	);
}
