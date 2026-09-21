import { useState } from "react";
import {
	useInfiniteQuery,
	useMutation,
	useQuery,
	useQueryClient,
} from "@tanstack/react-query";
import { agentPlatform } from "@/services/agentPlatform";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SearchBox } from "@/components/search/SearchBox";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { SuiteEditor } from "@/components/agents/evaluation/SuiteEditor";
import { CaseEditor } from "@/components/agents/evaluation/CaseEditor";
import { TestDesigner } from "@/components/agents/evaluation/TestDesigner";
import { summarizeExpectation } from "@/components/agents/evaluation/expectationSummaries";
import {
	EvidenceJson,
	PlatformError,
} from "@/components/agents/evaluation/PlatformEvidence";
import type { components } from "@/lib/v1";

/**
 * SuiteWorkspace — saved test suites for one Agent (Tests tab content).
 *
 * Suite select/create, publish, generated drafts with explicit
 * accept, and guided test authoring (finding-linked when a finding is
 * passed in). Extracted from the retired Studio page; behavior unchanged.
 */
export function SuiteWorkspace({
	agentId,
	agentName,
	organizationId,
	suiteId,
	designerId,
	findingId,
	onNavigate,
}: {
	agentId: string;
	agentName: string;
	organizationId: string | null;
	suiteId: string;
	designerId?: string;
	findingId?: string;
	onNavigate: (values: Record<string, string | undefined>) => void;
}) {
	const client = useQueryClient();
	const [createOpen, setCreateOpen] = useState(false);
	const [suiteName, setSuiteName] = useState("");
	const [suiteDescription, setSuiteDescription] = useState("");
	const [suiteSearch, setSuiteSearch] = useState("");
	const [caseSearch, setCaseSearch] = useState("");
	const [editing, setEditing] = useState<
		components["schemas"]["EvaluationCasePublic"] | "new" | null
	>(null);
	const [confirmPublish, setConfirmPublish] = useState(false);
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
	const create = useMutation({
		mutationFn: () =>
			agentPlatform.createSuite({
				name: suiteName,
				description: suiteDescription,
				agent_id: agentId,
				organization_id: organizationId,
			}),
		onSuccess: (data) => {
			refresh();
			onNavigate({
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
	const linkedFinding = useQuery({
		queryKey: ["agent-platform", "finding", findingId],
		queryFn: () => agentPlatform.finding(findingId!),
		enabled: !!findingId,
	});
	const findingTitle = linkedFinding.data
		? `Repro: ${linkedFinding.data.description.slice(0, 60)}`
		: undefined;
	function clearFinding() {
		onNavigate({ finding: undefined });
	}
	const frozen = suite.data?.status === "published";
	const mismatch = suite.data && suite.data.agent_id !== agentId;
	const loadedSuites = (suites.data?.pages.flat() ?? []).filter(
		(item) => item.agent_id === agentId,
	);
	const suiteQuery = suiteSearch.trim().toLowerCase();
	const matchingSuites = suiteQuery
		? loadedSuites.filter((item) =>
				`${item.name} ${item.status}`.toLowerCase().includes(suiteQuery),
			)
		: loadedSuites;
	const caseQuery = caseSearch.trim().toLowerCase();
	const matchingCases = caseQuery
		? (cases.data ?? []).filter((item) =>
				`${item.name} ${item.provenance}`
					.toLowerCase()
					.includes(caseQuery),
			)
		: (cases.data ?? []);
	return (
		<div className="space-y-6">
			<section aria-label="Suite selection" className="space-y-3">
				<ListToolbar>
					<div className="min-w-0 flex-1 sm:max-w-xs">
						<Label htmlFor="workspace-suite" className="sr-only">
							Suite
						</Label>
						<select
							id="workspace-suite"
							aria-label="Suite"
							className="h-11 w-full rounded-md border bg-background px-3 text-sm lg:h-10"
							value={suiteId}
							onChange={(event) => {
								onNavigate({
									suite: event.target.value,
									execution: undefined,
									designer: undefined,
								});
								setEditing(null);
								setConfirmPublish(false);
							}}
						>
							<option value="">Choose a suite</option>
							{matchingSuites.map((item) => (
								<option value={item.id} key={item.id}>
									{item.name} · {item.status} · v
									{item.version}
								</option>
							))}
						</select>
					</div>
					<SearchBox
						aria-label="Search suites"
						placeholder="Search suites"
						value={suiteSearch}
						onChange={setSuiteSearch}
						className="min-w-0 flex-1 sm:max-w-xs"
					/>
					<Button
						variant="outline"
						className="min-h-11"
						onClick={() => setCreateOpen((value) => !value)}
						aria-expanded={createOpen}
					>
						{createOpen ? "Close" : "New suite"}
					</Button>
				</ListToolbar>
				{suiteQuery && !matchingSuites.length && (
					<p className="text-sm text-muted-foreground">
						No loaded suites match your search.
					</p>
				)}
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
				{createOpen && (
					<form
						aria-label="New suite"
						className="space-y-3 rounded-lg border p-4"
						onSubmit={(event) => {
							event.preventDefault();
							create.mutate();
						}}
					>
						<div>
							<Label htmlFor="suite-name">Suite name</Label>
							<Input
								id="suite-name"
								required
								value={suiteName}
								onChange={(event) =>
									setSuiteName(event.target.value)
								}
							/>
						</div>
						<div>
							<Label htmlFor="suite-description">
								Description
							</Label>
							<Input
								id="suite-description"
								value={suiteDescription}
								onChange={(event) =>
									setSuiteDescription(event.target.value)
								}
							/>
						</div>
						<PlatformError error={create.error} />
						<Button disabled={create.isPending}>
							{create.isPending ? "Creating…" : "Create suite"}
						</Button>
					</form>
				)}
			</section>
			{mismatch ? (
				<p role="alert">
					This suite belongs to a different Agent context. Choose
					the matching suite before continuing.
				</p>
			) : (
				<>
					<PlatformError
						error={suite.error}
						retry={() => void suite.refetch()}
					/>
					{!suiteId && (
						<div className="rounded-lg border border-dashed p-8 text-center">
							<h3 className="text-lg font-semibold">
								Start with a suite
							</h3>
							<p className="mx-auto mt-2 max-w-prose text-sm text-muted-foreground">
								Group the situations {agentName} should
								handle. Add a test or generate drafts,
								review the responses and expectations,
								then save the suite version for repeatable
								comparisons.
							</p>
						</div>
					)}
					{suite.data && (
						<>
							{findingId && (
								<section
									aria-label="Linked finding"
									className="space-y-2 rounded-lg border p-4"
								>
									<div className="flex flex-wrap items-center justify-between gap-2">
										<h4 className="font-semibold">
											Drafting from finding
										</h4>
										<Button
											size="sm"
											variant="ghost"
											onClick={clearFinding}
										>
											Clear finding
										</Button>
									</div>
									<PlatformError
										error={linkedFinding.error}
										retry={() =>
											void linkedFinding.refetch()
										}
									/>
									{linkedFinding.data && (
										<>
											<p className="text-sm">
												{
													linkedFinding.data
														.description
												}
											</p>
											{linkedFinding.data
												.expected_behavior && (
												<p className="text-sm text-muted-foreground">
													Expected:{" "}
													{
														linkedFinding.data
															.expected_behavior
													}
												</p>
											)}
											<p className="text-xs text-muted-foreground">
												Source:{" "}
												{
													linkedFinding.data
														.source_kind
												}
												{linkedFinding.data
													.source_run_id &&
													` · run ${linkedFinding.data.source_run_id.slice(0, 8)}`}
												{linkedFinding.data
													.external_ref &&
													` · ${linkedFinding.data.external_ref}`}
											</p>
										</>
									)}
									{frozen && (
										<div className="space-y-2 border-t pt-3">
											<p className="text-sm text-muted-foreground">
												Published suites are
												read-only. Save a new
												editable version below to
												add this case.
											</p>
											<Button
												variant="outline"
												onClick={() => {
													setSuiteName(
														`${suite.data.name} (next version)`,
													);
													setCreateOpen(true);
												}}
											>
												New editable version
											</Button>
										</div>
									)}
								</section>
							)}
							<div className="flex flex-wrap items-center justify-between gap-3">
								<div>
									<h3 className="text-xl font-semibold">
										{suite.data.name}
									</h3>
									<p className="text-sm text-muted-foreground">
										{suite.data.description} · Version{" "}
										{suite.data.version} ·{" "}
										{frozen
											? "Published · saved version"
											: "Draft suite"}{" "}
										· {cases.data?.length ?? 0}{" "}
										{(cases.data?.length ?? 0) === 1
											? "test"
											: "tests"}
									</p>
								</div>
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
										Publish suite version
									</Button>
								)}
							</div>
							{!frozen && (
								<SuiteEditor
									key={`${suiteId}:${suite.data.version}`}
									suite={suite.data}
									onSaved={refresh}
								/>
							)}
							{confirmPublish && (
								<section className="space-y-3 rounded-md border p-4">
									<h4 className="font-semibold">
										Save this suite version for test runs?
									</h4>
									<p className="text-sm text-muted-foreground">
										Saving a version locks the suite and
										its cases. Only accepted, enabled
										cases run. Review or accept any
										remaining drafts before publishing.
									</p>
									<PlatformError error={publish.error} />
									<div className="flex gap-2">
										<Button
											disabled={publish.isPending}
											onClick={() => publish.mutate()}
										>
											Save suite version
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
							{!frozen && (
								<details key={`designer:${designerId ?? "idle"}`} open={designerId ? true : undefined}>
									<summary className="w-fit cursor-pointer text-sm font-medium">
										Generate tests
									</summary>
									<div className="mt-3 rounded-lg border p-4">
										<TestDesigner
											key={suiteId}
											suiteId={suiteId}
											agentId={agentId}
											runId={designerId}
											onQueued={(runId) =>
												onNavigate({ designer: runId })
											}
										/>
									</div>
								</details>
							)}
							<section className="space-y-4 border-t pt-5" aria-label="Tests">
								<div className="flex flex-wrap items-center justify-between gap-3">
									<h4 className="text-lg font-semibold">
										Tests
									</h4>
								</div>
								<ListToolbar>
									<SearchBox
										aria-label="Search tests"
										placeholder="Search tests"
										value={caseSearch}
										onChange={setCaseSearch}
										className="min-w-0 flex-1 sm:max-w-xs"
									/>
									{!frozen && (
										<Button
											variant="outline"
											className="min-h-11"
											onClick={() => setEditing("new")}
										>
											Add test
										</Button>
									)}
								</ListToolbar>
								{caseQuery && !matchingCases.length && (
									<p className="text-sm text-muted-foreground">
										No loaded tests match your search.
									</p>
								)}
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
												? `new:${findingId ?? ""}`
												: editing.id
										}
										suiteId={suiteId}
										draft={
											editing === "new"
												? undefined
												: editing
										}
										findingId={findingId}
										initialName={
											editing === "new"
												? findingTitle
												: undefined
										}
										onSaved={() => {
											setEditing(null);
											clearFinding();
											refresh();
										}}
										onCancel={() => setEditing(null)}
									/>
								)}
								{cases.data?.length === 0 && !editing && (
									<p className="py-6 text-sm text-muted-foreground">
										No tests yet. Generate tests or add
										the first one.
									</p>
								)}
								<ul className="divide-y rounded-lg border">
									{matchingCases.map((item) => (
										<li
											key={item.id}
											className="space-y-3 p-4"
										>
											<div className="flex flex-wrap items-center justify-between gap-3">
												<div>
													<h5 className="font-medium">
														{item.name}{" "}
														<span className="font-normal text-muted-foreground">
															v{item.version}
														</span>
													</h5>
													<p className="mt-1 text-xs text-muted-foreground">
														{item.accepted
															? "Accepted"
															: "Draft · needs review"}{" "}
														· {item.provenance}{" "}
														· {item.repetitions}{" "}
														repetition(s)
													</p>
												</div>
												{!item.accepted && !frozen && (
													<Button
														variant="outline"
														onClick={() =>
															setEditing(item)
														}
													>
														Edit test
													</Button>
												)}
											</div>
											<details>
												<summary className="cursor-pointer text-sm font-medium">
													Inspect scenario, tool
													responses and expectations
												</summary>
												<div className="mt-3 space-y-4">
													<CaseInspection
														item={item}
													/>
													<details>
														<summary className="cursor-pointer text-xs text-muted-foreground">
															Advanced
														</summary>
														<div className="mt-2 space-y-3">
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
																label={`${item.name} expectations`}
																value={
																	item.assertions
																}
															/>
														</div>
													</details>
													{!item.accepted &&
														!frozen && (
															<div className="space-y-2">
																<p className="text-sm text-muted-foreground">
																	Accepting
																	saves a
																	new
																	version.
																	Future
																	comparisons
																	reuse
																	these
																	exact
																	tests.
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
																	Accept and
																	save{" "}
																	{item.name}
																</Button>
															</div>
														)}
												</div>
											</details>
										</li>
									))}
								</ul>
							</section>
						</>
					)}
				</>
			)}
		</div>
	);
}

/**
 * Readable saved-test inspection: scenario message, configured tool
 * responses, and expectation summaries. Raw type/JSON stays under
 * Advanced; unknown definitions render losslessly as their type name.
 */
export function CaseInspection({
	item,
}: {
	item: components["schemas"]["EvaluationCasePublic"];
}) {
	const input =
		item.input && typeof item.input === "object" && !Array.isArray(item.input)
			? (item.input as Record<string, unknown>)
			: null;
	const message =
		input && typeof input.message === "string" ? input.message : null;
	const fixture =
		item.fixture && typeof item.fixture === "object" && !Array.isArray(item.fixture)
			? (item.fixture as Record<string, unknown>)
			: null;
	const allowed = Array.isArray(fixture?.allowed_tools)
		? (fixture?.allowed_tools as unknown[])
		: [];
	const rules = Array.isArray(fixture?.rules)
		? (fixture?.rules as Record<string, unknown>[])
		: [];
	const assertions = Array.isArray(item.assertions)
		? (item.assertions as components["schemas"]["EvaluationAssertion"][])
		: [];
	return (
		<div className="space-y-4 text-sm">
			<div>
				<h5 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
					Scenario
				</h5>
				{message ? (
					<p className="break-words">{message}</p>
				) : (
					<p className="text-muted-foreground">
						This test has no message field. See Advanced for the
						full input.
					</p>
				)}
			</div>
			<div>
				<h5 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
					Tool responses
				</h5>
				{!allowed.length && !rules.length ? (
					<p className="text-muted-foreground">
						No tool responses configured.
					</p>
				) : (
					<ul className="space-y-1">
						{allowed.map((toolName, index) => (
							<li key={index} className="break-words">
								<span className="font-medium">
									{String(toolName)}
								</span>{" "}
								<span className="text-muted-foreground">
									{rules.some(
										(rule) => rule.tool === toolName,
									)
										? "responds with a configured result"
										: "fails when called (no response configured)"}
								</span>
							</li>
						))}
						{rules
							.filter((rule) => !allowed.includes(rule.tool))
							.map((rule, index) => (
								<li key={`rule-${index}`} className="break-words">
									<span className="font-medium">
										{String(rule.tool ?? "unknown tool")}
									</span>{" "}
									<span className="text-muted-foreground">
										responds to{" "}
										{JSON.stringify(rule.match_args ?? {})}
									</span>
								</li>
							))}
					</ul>
				)}
			</div>
			<div>
				<h5 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
					Expected behavior
				</h5>
				{!assertions.length ? (
					<p className="text-muted-foreground">
						No expectations configured.
					</p>
				) : (
					<ul className="space-y-1">
						{assertions.map((assertion, index) => {
							const label =
								typeof assertion.label === "string" &&
								assertion.label
									? assertion.label
									: assertion.type;
							const summary = summarizeExpectation(assertion);
							return (
								<li key={index} className="break-words">
									<span className="font-medium">{label}</span>
									{summary && summary !== label ? (
										<span className="text-muted-foreground">
											{" "}
											· {summary}
										</span>
									) : null}
									{!summary ? (
										<span className="text-muted-foreground">
											{" "}
											· {assertion.type}
										</span>
									) : null}
								</li>
							);
						})}
					</ul>
				)}
			</div>
		</div>
	);
}
