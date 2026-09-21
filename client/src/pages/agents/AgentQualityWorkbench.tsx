import { useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	ArrowLeft,
	CircleCheck,
	CircleX,
	Clock3,
	FlaskConical,
	History,
	ScanSearch,
	TriangleAlert,
	type LucideIcon,
} from "lucide-react";

import { PageWorkspace } from "@/components/layout/PageWorkspace";
import { PageLoader } from "@/components/PageLoader";
import { ModelProfileSelector } from "@/components/ai/ModelProfileSelector";
import { QualityHeader } from "@/components/agents/QualityHeader";
import { AgentWorkbenchFrame } from "@/components/agents/workbench/AgentWorkbenchFrame";
import { WorkbenchCollectionToolbar } from "@/components/agents/workbench/WorkbenchCollectionToolbar";
import { WorkbenchRow } from "@/components/agents/workbench/WorkbenchRow";
import { ChangesWorkspace } from "@/components/agents/evaluation/ChangesWorkspace";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useAgent, useAgents } from "@/hooks/useAgents";
import { useInfiniteAgentRuns, type AgentRun } from "@/services/agentRuns";
import { agentPlatform } from "@/services/agentPlatform";
import { cn, formatCost } from "@/lib/utils";
import type { components } from "@/lib/v1";

import { FleetReadError } from "./FleetReadError";

type Schema = components["schemas"];
type AgentTest = Schema["AgentTestPublic"];
type AgentTestLatest = Schema["AgentTestLatestPublic"];
type Finding = Schema["FindingPublic"];
type Review = Schema["AgentReviewDefinitionPublic"];
type RecordedResultsPage = Schema["RecordedEvaluationResultsPage"];
type QualityUsage = Schema["QualityUsageBreakdownResponse"];
type QualityRun = AgentRun & {
	asked?: string | null;
	did?: string | null;
};

type Collection = "tests" | "findings" | "reviews" | "runs";

const COLLECTION_META: Record<
	Collection,
	{
		label: string;
		group: string;
		description: string;
		icon: LucideIcon;
	}
> = {
	findings: {
		label: "Findings",
		group: "Act",
		description:
			"Investigate problems and opportunities surfaced by runs and Reviews.",
		icon: TriangleAlert,
	},
	tests: {
		label: "Tests",
		group: "Validate",
		description: "Validate expected agent behavior with repeatable tests.",
		icon: FlaskConical,
	},
	reviews: {
		label: "Reviews",
		group: "Automate",
		description:
			"Automate plain-English checks over completed runs to surface Findings.",
		icon: ScanSearch,
	},
	runs: {
		label: "Run History",
		group: "Evidence",
		description:
			"Inspect the lifecycle and evidence from Workbench operations.",
		icon: History,
	},
};

const COLLECTIONS: Collection[] = ["findings", "tests", "reviews", "runs"];

function collectionFromParams(params: URLSearchParams): Collection {
	const collection = params.get("collection");
	if (
		collection === "tests" ||
		collection === "findings" ||
		collection === "reviews" ||
		collection === "runs"
	) {
		return collection;
	}

	const oldTab = params.get("tab");
	if (oldTab === "evidence") return "findings";
	if (oldTab === "tests" || oldTab === "changes") return "tests";
	return "tests";
}

function lowerFirst(value: string): string {
	return value ? value[0].toLowerCase() + value.slice(1) : value;
}

function testNameFromExpected(expected: string, situation: string): string {
	const base =
		expected.trim() || situation.trim() || "match expected behavior";
	if (/^should\b/i.test(base)) return base;
	return `Should ${lowerFirst(base)}`;
}

function resultLabel(latest?: AgentTestLatest): string {
	const simulation = latest?.simulation?.status ?? "none";
	const recorded = latest?.recorded?.outcome ?? "none";
	return `Simulation: ${simulation} · Recorded: ${recorded}`;
}

function includesText(value: unknown, query: string): boolean {
	return JSON.stringify(value ?? "")
		.toLowerCase()
		.includes(query.toLowerCase());
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): string[] | undefined {
	return Array.isArray(value) &&
		value.every((item) => typeof item === "string")
		? value
		: undefined;
}

function parseAdvancedJson(value: string): {
	fields: Partial<Schema["AgentTestCreate"]>;
	error: string | null;
} {
	const trimmed = value.trim();
	if (!trimmed) return { fields: {}, error: null };
	let parsed: unknown;
	try {
		parsed = JSON.parse(trimmed);
	} catch {
		return { fields: {}, error: "Advanced JSON must be valid JSON." };
	}
	if (!isRecord(parsed)) {
		return { fields: {}, error: "Advanced JSON must be a JSON object." };
	}

	const fields: Partial<Schema["AgentTestCreate"]> = {};
	const expectedTools = stringArray(parsed.expected_tools);
	if (expectedTools) fields.expected_tools = expectedTools;
	const forbiddenTools = stringArray(parsed.forbidden_tools);
	if (forbiddenTools) fields.forbidden_tools = forbiddenTools;
	if (isRecord(parsed.fixture)) fields.fixture = parsed.fixture;
	if (isRecord(parsed.simulator_policy))
		fields.simulator_policy = parsed.simulator_policy;
	if (Array.isArray(parsed.assertions) && parsed.assertions.every(isRecord)) {
		fields.assertions = parsed.assertions;
	}
	if (isRecord(parsed.output_schema))
		fields.output_schema = parsed.output_schema;
	if (
		typeof parsed.repetitions === "number" &&
		Number.isInteger(parsed.repetitions) &&
		parsed.repetitions > 0
	) {
		fields.repetitions = parsed.repetitions;
	}
	if (isRecord(parsed.scoring_policy))
		fields.scoring_policy = parsed.scoring_policy;
	const tags = stringArray(parsed.tags);
	if (tags) fields.tags = tags;

	return { fields, error: null };
}

export type AgentWorkbenchScope = "agent" | "fleet";

export function AgentQualityWorkbench({
	scope = "agent",
}: {
	scope?: AgentWorkbenchScope;
}) {
	return <AgentQualityWorkbenchContent scope={scope} />;
}

function AgentQualityWorkbenchContent({
	scope,
}: {
	scope: AgentWorkbenchScope;
}) {
	const { id: routeAgentId } = useParams<{ id: string }>();
	const [params, setParams] = useSearchParams();
	const queryClient = useQueryClient();
	const isFleet = scope === "fleet";
	const effectiveAgentId = isFleet
		? (params.get("agent") ?? "")
		: routeAgentId;
	const collection = isFleet
		? fleetCollectionFromParams(params.get("collection"))
		: collectionFromParams(params);
	const selectedKey = params.get("selected") ?? "";
	const findingId = params.get("finding");
	const suiteId = params.get("suite") ?? "";
	const candidateId = params.get("candidate") ?? "";
	const matrixId = params.get("matrix") ?? "";
	const executionId = params.get("execution") ?? "";
	const recordedId = params.get("recorded") ?? "";
	const profileIds = (params.get("profiles") ?? "")
		.split(",")
		.filter(Boolean);
	const hasChangesContext = Boolean(candidateId || matrixId || executionId);

	const [agentSearch, setAgentSearch] = useState("");
	const search = isFleet ? (params.get("search") ?? "") : agentSearch;
	const [isCreatingTest, setIsCreatingTest] = useState(false);
	const [dismissedInspectorContext, setDismissedInspectorContext] =
		useState("");
	const [selectedTests, setSelectedTests] = useState<Set<string>>(
		() => new Set(),
	);
	const [profileId, setProfileId] = useState("");
	const [queuedExecution, setQueuedExecution] = useState("");
	const [selectionError, setSelectionError] = useState("");
	const [situation, setSituation] = useState("");
	const [expectedBehavior, setExpectedBehavior] = useState("");
	const [advancedJson, setAdvancedJson] = useState("");
	const [advancedJsonError, setAdvancedJsonError] = useState("");
	const queuedSuiteIdRef = useRef("");
	const inspectorContext = [
		selectedKey,
		findingId,
		recordedId,
		suiteId,
		candidateId,
		matrixId,
		executionId,
		isCreatingTest,
	].join(":");
	const isInspectorDismissed = dismissedInspectorContext === inspectorContext;

	function updateParams(values: Record<string, string | undefined>) {
		const next = new URLSearchParams(params);
		Object.entries(values).forEach(([key, value]) => {
			if (value) next.set(key, value);
			else next.delete(key);
		});
		if ("collection" in values && values.collection !== collection) {
			next.delete("tab");
			next.delete("selected");
		}
		if (
			["suite", "candidate", "profiles"].some((key) => key in values) &&
			!("execution" in values)
		) {
			next.delete("matrix");
			next.delete("execution");
		}
		if (values.matrix) next.delete("execution");
		if (values.execution) next.delete("matrix");
		setParams(next, { replace: true });
	}

	const {
		data: agent,
		isLoading: agentLoading,
		isError: agentError,
		isFetching: agentFetching,
		refetch: refetchAgent,
	} = useAgent(isFleet ? undefined : effectiveAgentId);
	const { data: agentList } = useAgents(undefined, {
		includeInactive: true,
		includeStats: false,
	});
	const agents = (agentList ?? []) as FleetAgentSummary[];
	const agentName = (id: string) =>
		agents.find((agent) => agent.id === id)?.name ?? id;

	const testsQuery = useQuery({
		queryKey: ["agent-platform", "agent-tests", effectiveAgentId],
		queryFn: () =>
			agentPlatform.agentTests(effectiveAgentId!, {
				limit: 50,
				offset: 0,
			}),
		enabled: !!effectiveAgentId && (!isFleet || collection === "tests"),
		retry: false,
	});
	const latestQuery = useQuery({
		queryKey: ["agent-platform", "agent-tests-latest", effectiveAgentId],
		queryFn: () =>
			agentPlatform.latestAgentTests(effectiveAgentId!, {
				limit: 50,
				offset: 0,
			}),
		enabled: !!effectiveAgentId && (!isFleet || collection === "tests"),
		retry: false,
	});
	const findingsQuery = useQuery({
		queryKey: [
			"agent-platform",
			"findings",
			isFleet ? "fleet" : "agent",
			effectiveAgentId,
			search,
			params.get("status"),
			params.get("kind"),
		],
		queryFn: async (): Promise<Finding[]> => {
			if (!isFleet) return agentPlatform.findings(effectiveAgentId!);
			const page = await agentPlatform.searchFindings({
				offset: 0,
				limit: 50,
				q: search || undefined,
				status: params.get("status") || undefined,
				finding_kind: params.get("kind") || undefined,
				agent_id: effectiveAgentId || undefined,
			});
			return page.items;
		},
		enabled: isFleet ? collection === "findings" : !!effectiveAgentId,
		retry: false,
	});
	const referencedFindingQuery = useQuery({
		queryKey: ["agent-platform", "finding", findingId],
		queryFn: () => agentPlatform.finding(findingId!),
		enabled: isFleet && collection === "tests" && !!findingId,
		retry: false,
	});
	const reviewsQuery = useQuery({
		queryKey: ["agent-platform", "reviews", effectiveAgentId, isFleet],
		queryFn: () =>
			agentPlatform.reviews({
				agent_id: effectiveAgentId!,
				status: isFleet ? undefined : "active",
				limit: 50,
				offset: 0,
			}),
		enabled: !!effectiveAgentId && (!isFleet || collection === "reviews"),
		retry: false,
	});
	const runsQuery = useInfiniteAgentRuns({
		agentId: isFleet ? undefined : effectiveAgentId,
		pageSize: 50,
		enabled: !isFleet,
	});
	const recordedResultsQuery = useQuery({
		queryKey: ["agent-platform", "recorded-results", recordedId],
		queryFn: () =>
			agentPlatform.recordedEvaluationResults(recordedId, {
				limit: 50,
				offset: 0,
			}),
		enabled: !!recordedId,
		retry: false,
	});
	const recordedUsageQuery = useQuery({
		queryKey: ["agent-platform", "recorded-usage", recordedId],
		queryFn: () =>
			agentPlatform.recordedEvaluationUsage(recordedId, {
				limit: 50,
				offset: 0,
			}),
		enabled: !!recordedId,
		retry: false,
	});

	const latestByLogicalId = useMemo(() => {
		return new Map(
			(latestQuery.data?.items ?? []).map((latest) => [
				latest.logical_test_id,
				latest,
			]),
		);
	}, [latestQuery.data?.items]);

	const tests = useMemo(
		() => testsQuery.data?.items ?? [],
		[testsQuery.data?.items],
	);
	const findings = useMemo(
		() => findingsQuery.data ?? [],
		[findingsQuery.data],
	);
	const reviews = useMemo(
		() => reviewsQuery.data?.items ?? [],
		[reviewsQuery.data?.items],
	);
	const runs = useMemo(
		() =>
			(runsQuery.data?.pages.flatMap((page) => page.items) as
				QualityRun[] | undefined) ?? [],
		[runsQuery.data?.pages],
	);
	const collectionCounts: Record<Collection, number> = {
		findings: findings.length,
		tests: tests.length,
		reviews: reviews.length,
		runs: runs.length,
	};
	const collectionItems = (isFleet ? FLEET_COLLECTIONS : COLLECTIONS).map(
		(value) => {
			const meta = COLLECTION_META[value];
			const Icon = meta.icon;
			return {
				value,
				label: meta.label,
				group: meta.group,
				count: collectionCounts[value],
				icon: <Icon className="size-4" />,
			};
		},
	);
	const selectedCollection = COLLECTION_META[collection];
	const findingContext =
		findings.find((finding) => finding.id === findingId) ??
		referencedFindingQuery.data;
	const isLinkedFleetFindingUnavailable =
		isFleet &&
		collection === "tests" &&
		!!findingId &&
		!findingContext &&
		!referencedFindingQuery.isLoading;
	const testCreationAgentId = findingContext?.agent_id ?? effectiveAgentId;
	const selectedTestItems = tests.filter((test) =>
		selectedTests.has(test.logical_test_id),
	);
	const changesWorkspace =
		!isFleet && hasChangesContext && effectiveAgentId ? (
			<ChangesWorkspace
				agentId={effectiveAgentId}
				suiteId={suiteId}
				candidateId={candidateId}
				profileIds={profileIds}
				matrixId={matrixId}
				executionId={executionId}
				onNavigate={updateParams}
			/>
		) : undefined;

	const selectedItem = useMemo(() => {
		const [, selectedId] = selectedKey.split(":", 2);
		if (!selectedId) return null;
		if (collection === "tests")
			return (
				tests.find((test) => test.logical_test_id === selectedId) ??
				null
			);
		if (collection === "findings")
			return (
				findings.find((finding) => finding.id === selectedId) ?? null
			);
		if (collection === "reviews")
			return reviews.find((review) => review.id === selectedId) ?? null;
		return runs.find((run) => run.id === selectedId) ?? null;
	}, [collection, findings, reviews, runs, selectedKey, tests]);
	const isTestCreationOpen =
		collection === "tests" &&
		!selectedItem &&
		(isCreatingTest || Boolean(findingContext));

	const filteredTests = tests.filter((test) => includesText(test, search));
	const filteredFindings = findings.filter((finding) =>
		includesText(finding, search),
	);
	const filteredReviews = reviews.filter((review) =>
		includesText(review, search),
	);
	const filteredRuns = runs.filter((run) => includesText(run, search));

	const createTest = useMutation({
		mutationFn: (body: Schema["AgentTestCreate"]) =>
			agentPlatform.createAgentTest(testCreationAgentId!, body),
		onSuccess: async () => {
			setSituation("");
			setExpectedBehavior("");
			setAdvancedJson("");
			setIsCreatingTest(false);
			updateParams({ finding: undefined, selected: undefined });
			await queryClient.invalidateQueries({
				queryKey: [
					"agent-platform",
					"agent-tests",
					testCreationAgentId,
				],
			});
		},
	});

	const runTests = useMutation({
		mutationFn: (body: Schema["AgentTestsRunCreate"]) =>
			agentPlatform.runAgentTests(effectiveAgentId!, body),
		onSuccess: (result) => {
			setQueuedExecution(result.executionId);
			updateParams({
				execution: result.executionId,
				suite: queuedSuiteIdRef.current || undefined,
			});
		},
	});

	function reopenInspector() {
		setDismissedInspectorContext("");
	}

	function selectCollection(nextCollection: Collection) {
		reopenInspector();
		updateParams({
			collection: nextCollection,
			...(isFleet ? { selected: undefined, finding: undefined } : {}),
		});
		if (!isFleet) setAgentSearch("");
		setIsCreatingTest(false);
	}

	function selectListItem(kind: Collection, id: string) {
		reopenInspector();
		updateParams({
			selected: `${kind}:${id}`,
			finding: kind === "findings" ? id : undefined,
		});
		setIsCreatingTest(false);
	}

	function openTestCreation() {
		reopenInspector();
		setIsCreatingTest(true);
		updateParams({ selected: undefined });
	}

	function openTestCreationFromFinding(finding: Finding) {
		reopenInspector();
		setSituation(finding.description);
		setExpectedBehavior(finding.expected_behavior ?? "");
		updateParams({
			collection: "tests",
			finding: finding.id,
			...(isFleet ? { agent: finding.agent_id } : {}),
		});
	}

	function toggleTest(testId: string) {
		setSelectionError("");
		setSelectedTests((existing) => {
			const next = new Set(existing);
			if (next.has(testId)) next.delete(testId);
			else next.add(testId);
			return next;
		});
	}

	function submitTest() {
		if (!testCreationAgentId) return;
		const advanced = parseAdvancedJson(advancedJson);
		if (advanced.error) {
			setAdvancedJsonError(advanced.error);
			return;
		}
		setAdvancedJsonError("");
		const defaultAssertion = {
			type: "terminal_status",
			label: "Completes successfully",
			params: { status: "completed" },
		};
		const body: Schema["AgentTestCreate"] = {
			name: testNameFromExpected(expectedBehavior, situation),
			position: tests.length,
			enabled: true,
			input: {
				situation: situation.trim(),
				expected_behavior: expectedBehavior.trim(),
			},
			fixture: {},
			simulator_policy: {},
			assertions: [defaultAssertion],
			expected_tools: [],
			forbidden_tools: [],
			output_schema: null,
			repetitions: 1,
			scoring_policy: {},
			provenance: findingContext ? "finding" : "manual",
			provenance_run_ids: findingContext?.source_run_id
				? [findingContext.source_run_id]
				: [],
			finding_id: findingContext?.id ?? null,
			tags: [],
			...advanced.fields,
		};
		createTest.mutate(body);
	}

	function submitSimulation() {
		if (!effectiveAgentId || selectedTestItems.length === 0) return;
		const suiteIds = new Set(
			selectedTestItems.map((test) => test.origin_suite_id),
		);
		if (suiteIds.size > 1) {
			setSelectionError(
				"Choose tests from one collection before running a simulation.",
			);
			return;
		}
		setSelectionError("");
		queuedSuiteIdRef.current = selectedTestItems[0]?.origin_suite_id ?? "";
		runTests.mutate({
			selections: selectedTestItems.map((test) => ({
				case_id: test.case_id,
				case_version: test.version,
			})),
			profile_id: profileId || null,
			candidate_id: candidateId || null,
		});
	}

	if (!isFleet && !agent && agentLoading) return <PageLoader />;
	if (!isFleet && !agent && agentError) {
		return (
			<div className="space-y-4">
				<h1 className="font-display text-2xl font-semibold">
					Workbench
				</h1>
				<FleetReadError
					resource="agent"
					cached={false}
					pending={agentFetching}
					onRetry={() => {
						void refetchAgent();
					}}
				/>
			</div>
		);
	}

	return (
		<PageWorkspace
			className="mx-auto flex min-w-0 w-full max-w-[1400px] flex-col gap-5"
			data-agent-workbench=""
			data-testid="agent-workbench"
		>
			<div className="space-y-5" data-testid="quality-sticky-header">
				{isFleet ? (
					<div className="space-y-3">
						<Link
							to="/agents"
							className="inline-flex min-h-11 w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
						>
							<ArrowLeft aria-hidden="true" className="size-4" />
							Back to Agents
						</Link>
						<div>
							<h1 className="font-display text-2xl font-semibold tracking-tight">
								Agent Workbench
							</h1>
							<p className="mt-1 text-sm text-muted-foreground">
								Findings, tests, and reviews across the agent
								fleet.
							</p>
						</div>
					</div>
				) : (
					<QualityHeader
						agentId={effectiveAgentId}
						agentName={agent?.name}
					/>
				)}
				{!isFleet && agentError && (
					<FleetReadError
						resource="agent"
						cached={!!agent}
						pending={agentFetching}
						onRetry={() => {
							void refetchAgent();
						}}
					/>
				)}
			</div>

			<AgentWorkbenchFrame
				title={selectedCollection.label}
				description={selectedCollection.description}
				collections={collectionItems}
				collection={collection}
				onCollectionChange={(next) =>
					selectCollection(next as Collection)
				}
				toolbar={
					<WorkbenchCollectionToolbar
						collectionLabel={selectedCollection.label}
						search={search}
						onSearchChange={(value) =>
							isFleet
								? updateParams({ search: value || undefined })
								: setAgentSearch(value)
						}
						selectionCount={
							!isFleet && collection === "tests"
								? selectedTests.size
								: 0
						}
						secondaryAction={
							collection === "tests" && !!effectiveAgentId ? (
								<Button
									type="button"
									variant="outline"
									onClick={openTestCreation}
								>
									Add Test
								</Button>
							) : undefined
						}
						primaryAction={
							!isFleet && collection === "tests"
								? {
										type: "button",
										onClick: submitSimulation,
										disabled:
											selectedTestItems.length === 0 ||
											runTests.isPending,
										children: "Run Simulation",
									}
								: undefined
						}
					>
						{!isFleet && collection === "tests" ? (
							<div className="min-w-[12rem]">
								<ModelProfileSelector
									label="Profile"
									value={profileId}
									onValueChange={setProfileId}
									placeholder="Default assignment"
								/>
							</div>
						) : null}
						{isFleet ? (
							<FleetAgentFilter
								agents={agents}
								collection={collection}
								value={effectiveAgentId ?? "all"}
								onValueChange={(value) =>
									updateParams({
										agent:
											value === "all" ? undefined : value,
										selected: undefined,
										finding: undefined,
									})
								}
							/>
						) : null}
						{queuedExecution ? (
							<span className="text-sm font-medium text-foreground">
								Queued simulation {queuedExecution}
							</span>
						) : null}
					</WorkbenchCollectionToolbar>
				}
				inspector={
					isInspectorDismissed ? undefined : recordedId ? (
						<RecordedResultsPanel
							results={recordedResultsQuery.data}
							usage={recordedUsageQuery.data}
							isLoading={
								recordedResultsQuery.isLoading ||
								recordedUsageQuery.isLoading
							}
							isError={
								recordedResultsQuery.isError ||
								recordedUsageQuery.isError
							}
							onRetry={() => {
								void recordedResultsQuery.refetch();
								void recordedUsageQuery.refetch();
							}}
						/>
					) : changesWorkspace ? (
						changesWorkspace
					) : selectedItem ? (
						<Inspector
							collection={collection}
							item={selectedItem}
							agentId={
								isFleet && collection === "findings"
									? (selectedItem as Finding).agent_id
									: effectiveAgentId
							}
							latestByLogicalId={latestByLogicalId}
							onCreateTestFromFinding={
								collection === "findings"
									? openTestCreationFromFinding
									: undefined
							}
						/>
					) : isTestCreationOpen ? (
						<TestCreationPanel
							finding={findingContext}
							situation={situation}
							expectedBehavior={expectedBehavior}
							advancedJson={advancedJson}
							advancedJsonError={advancedJsonError}
							createError={
								createTest.error instanceof Error
									? createTest.error.message
									: createTest.isError
										? "Could not create test."
										: ""
							}
							isPending={createTest.isPending}
							onSituationChange={setSituation}
							onExpectedBehaviorChange={setExpectedBehavior}
							onAdvancedJsonChange={setAdvancedJson}
							onClearFinding={() => {
								setIsCreatingTest(false);
								updateParams({ finding: undefined });
							}}
							onCreate={submitTest}
						/>
					) : undefined
				}
				onCloseInspector={() => {
					setIsCreatingTest(false);
					setDismissedInspectorContext(inspectorContext);
				}}
			>
				{selectionError ? (
					<p className="border-b px-4 py-2 text-sm text-destructive">
						{selectionError}
					</p>
				) : null}
				{collection === "tests" &&
					(isLinkedFleetFindingUnavailable ? (
						<CollectionState
							tone="error"
							actionLabel="Retry Finding"
							onAction={() => {
								void referencedFindingQuery.refetch();
							}}
						>
							This linked Finding is unavailable. Retry to
							continue creating its test.
						</CollectionState>
					) : isFleet &&
					  findingId &&
					  referencedFindingQuery.isLoading ? (
						<CollectionState>
							Loading linked Finding…
						</CollectionState>
					) : effectiveAgentId ? (
						<TestsCollection
							tests={filteredTests}
							latestByLogicalId={latestByLogicalId}
							selectedTests={selectedTests}
							selectedKey={selectedKey}
							isLoading={testsQuery.isLoading}
							isError={testsQuery.isError || latestQuery.isError}
							onRetry={() => {
								void testsQuery.refetch();
								void latestQuery.refetch();
							}}
							onToggle={isFleet ? () => {} : toggleTest}
							agentName={
								isFleet
									? agentName(effectiveAgentId)
									: undefined
							}
							onAddTest={openTestCreation}
							showSelectionControl={!isFleet}
							onSelect={(test) =>
								selectListItem("tests", test.logical_test_id)
							}
						/>
					) : (
						<CollectionState>
							Choose an agent to view tests and add the first
							test.
						</CollectionState>
					))}
				{collection === "findings" && (
					<FindingsCollection
						findings={filteredFindings}
						agentName={isFleet ? agentName : undefined}
						selectedKey={selectedKey}
						isLoading={findingsQuery.isLoading}
						isError={findingsQuery.isError}
						onRetry={() => {
							void findingsQuery.refetch();
						}}
						onSelect={(finding) =>
							selectListItem("findings", finding.id)
						}
					/>
				)}
				{collection === "reviews" &&
					(effectiveAgentId ? (
						<ReviewsCollection
							reviews={filteredReviews}
							selectedKey={selectedKey}
							isLoading={reviewsQuery.isLoading}
							isError={reviewsQuery.isError}
							onRetry={() => {
								void reviewsQuery.refetch();
							}}
							onSelect={(review) =>
								selectListItem("reviews", review.id)
							}
							agentName={
								isFleet
									? agentName(effectiveAgentId)
									: undefined
							}
						/>
					) : (
						<CollectionState>
							Choose an agent to view its Reviews and the findings
							they create.
						</CollectionState>
					))}
				{!isFleet && collection === "runs" && (
					<RunsCollection
						runs={filteredRuns}
						selectedKey={selectedKey}
						isLoading={runsQuery.isLoading}
						isError={runsQuery.isError}
						onRetry={() => {
							void runsQuery.refetch();
						}}
						onSelect={(run) => selectListItem("runs", run.id)}
					/>
				)}
			</AgentWorkbenchFrame>
		</PageWorkspace>
	);
}

function TestsCollection({
	tests,
	latestByLogicalId,
	selectedTests,
	selectedKey,
	isLoading,
	isError,
	onToggle,
	onSelect,
	onRetry,
	agentName,
	onAddTest,
	showSelectionControl = true,
}: {
	tests: AgentTest[];
	latestByLogicalId: Map<string, AgentTestLatest>;
	selectedTests: Set<string>;
	selectedKey: string;
	isLoading: boolean;
	isError: boolean;
	onToggle: (id: string) => void;
	onSelect: (test: AgentTest) => void;
	onRetry: () => void;
	agentName?: string;
	onAddTest?: () => void;
	showSelectionControl?: boolean;
}) {
	if (isLoading) return <CollectionState>Loading tests…</CollectionState>;
	if (isError)
		return (
			<CollectionState
				tone="error"
				actionLabel="Retry Tests"
				onAction={onRetry}
			>
				Could not load tests.
			</CollectionState>
		);
	if (tests.length === 0)
		return (
			<CollectionState
				actionLabel={onAddTest ? "Add Test" : undefined}
				onAction={onAddTest}
			>
				No tests yet. Add a test to capture behavior you want to verify.
			</CollectionState>
		);
	return (
		<div role="list" aria-label="Tests collection">
			{tests.map((test) => {
				const latest = latestByLogicalId.get(test.logical_test_id);
				const latestOutcome =
					latest?.recorded?.outcome ??
					latest?.simulation?.status ??
					"none";
				const testFailed = latestOutcome === "failed";
				const testPassed =
					latestOutcome === "passed" || latestOutcome === "completed";
				const StatusIcon = testFailed
					? CircleX
					: testPassed
						? CircleCheck
						: Clock3;
				const statusLabel = testFailed
					? "Failed Test"
					: testPassed
						? "Passed Test"
						: "Not Run Test";
				return (
					<WorkbenchRow
						key={test.logical_test_id}
						title={test.name}
						leading={
							<StatusIcon
								className={cn(
									"size-4",
									testFailed && "text-destructive",
									testPassed &&
										"text-emerald-600 dark:text-emerald-400",
								)}
							/>
						}
						leadingLabel={statusLabel}
						meta={
							<>
								<span>{resultLabel(latest)}</span>
								{agentName ? (
									<>
										<span className="mx-1">·</span>
										{agentName}
									</>
								) : null}
								<span className="mx-1">·</span>v{test.version}
								{test.origin_suite_name ? (
									<>
										<span className="mx-1">·</span>
										{test.origin_suite_name}
									</>
								) : null}
							</>
						}
						selected={
							selectedKey === `tests:${test.logical_test_id}`
						}
						onSelect={() => onSelect(test)}
						selectionControl={
							showSelectionControl ? (
								<Checkbox
									checked={selectedTests.has(
										test.logical_test_id,
									)}
									onCheckedChange={() =>
										onToggle(test.logical_test_id)
									}
									aria-label={`Select ${test.name}`}
								/>
							) : undefined
						}
					/>
				);
			})}
		</div>
	);
}

function FindingsCollection({
	findings,
	selectedKey,
	isLoading,
	isError,
	onSelect,
	onRetry,
	agentName,
}: {
	findings: Finding[];
	selectedKey: string;
	isLoading: boolean;
	isError: boolean;
	onSelect: (finding: Finding) => void;
	onRetry: () => void;
	agentName?: (agentId: string) => string;
}) {
	if (isLoading) return <CollectionState>Loading findings…</CollectionState>;
	if (isError)
		return (
			<CollectionState
				tone="error"
				actionLabel="Retry Findings"
				onAction={onRetry}
			>
				Could not load findings.
			</CollectionState>
		);
	if (findings.length === 0)
		return (
			<CollectionState>
				No findings yet. Findings come from runs and Reviews that need
				follow-up.
			</CollectionState>
		);
	return (
		<div role="list" aria-label="Findings collection">
			{findings.map((finding) => {
				const sourceLabel = `${finding.source_kind.charAt(0).toUpperCase()}${finding.source_kind.slice(1).replaceAll("_", " ")} source`;
				const statusLabel = `${finding.status.charAt(0).toUpperCase()}${finding.status.slice(1)} Finding`;
				return (
					<WorkbenchRow
						key={finding.id}
						title={finding.description}
						leading={
							<TriangleAlert className="size-4 text-amber-600 dark:text-amber-400" />
						}
						leadingLabel={statusLabel}
						meta={
							<>
								<Badge variant="warning">{sourceLabel}</Badge>
								{agentName ? (
									<>
										<span className="mx-1">·</span>
										{agentName(finding.agent_id)}
									</>
								) : null}
								{finding.expected_behavior ? (
									<>
										<span className="mx-1">·</span>
										Expected: {finding.expected_behavior}
									</>
								) : null}
							</>
						}
						selected={selectedKey === `findings:${finding.id}`}
						onSelect={() => onSelect(finding)}
					/>
				);
			})}
		</div>
	);
}

function ReviewsCollection({
	reviews,
	selectedKey,
	isLoading,
	isError,
	onSelect,
	onRetry,
	agentName,
}: {
	reviews: Review[];
	selectedKey: string;
	isLoading: boolean;
	isError: boolean;
	onSelect: (review: Review) => void;
	onRetry: () => void;
	agentName?: string;
}) {
	if (isLoading) return <CollectionState>Loading reviews…</CollectionState>;
	if (isError)
		return (
			<CollectionState
				tone="error"
				actionLabel="Retry Reviews"
				onAction={onRetry}
			>
				Could not load reviews.
			</CollectionState>
		);
	if (reviews.length === 0)
		return (
			<CollectionState>
				No Reviews yet. Create a Review from completed runs to surface
				durable findings.
			</CollectionState>
		);
	return (
		<div role="list" aria-label="Reviews collection">
			{reviews.map((review) => (
				<WorkbenchRow
					key={review.id}
					title={review.name}
					leading={<ScanSearch className="size-4 text-primary" />}
					leadingLabel={`${review.status.charAt(0).toUpperCase()}${review.status.slice(1)} Review`}
					meta={
						<>
							<Badge variant="outline">Surfaces Findings</Badge>
							{agentName ? (
								<>
									<span className="mx-1">·</span>
									{agentName}
								</>
							) : null}
							<span className="mx-1">·</span>Version{" "}
							{review.latest_version}
							<span className="mx-1">·</span>
							{review.status}
						</>
					}
					selected={selectedKey === `reviews:${review.id}`}
					onSelect={() => onSelect(review)}
				/>
			))}
		</div>
	);
}

function RunsCollection({
	runs,
	selectedKey,
	isLoading,
	isError,
	onSelect,
	onRetry,
}: {
	runs: QualityRun[];
	selectedKey: string;
	isLoading: boolean;
	isError: boolean;
	onSelect: (run: QualityRun) => void;
	onRetry: () => void;
}) {
	if (isLoading)
		return <CollectionState>Loading run history…</CollectionState>;
	if (isError)
		return (
			<CollectionState
				tone="error"
				actionLabel="Retry Run History"
				onAction={onRetry}
			>
				Could not load run history.
			</CollectionState>
		);
	if (runs.length === 0)
		return (
			<CollectionState>
				No run history yet. Run a Test or Review to create evidence
				here.
			</CollectionState>
		);
	return (
		<div role="list" aria-label="Run History collection">
			{runs.map((run) => (
				<WorkbenchRow
					key={run.id}
					title={run.asked ?? run.trigger_type}
					leading={<History className="size-4 text-primary" />}
					leadingLabel={`${run.status} Workbench Run`}
					meta={`${run.status} · ${run.created_at}`}
					selected={selectedKey === `runs:${run.id}`}
					onSelect={() => onSelect(run)}
				/>
			))}
		</div>
	);
}

function TestCreationPanel({
	finding,
	situation,
	expectedBehavior,
	advancedJson,
	advancedJsonError,
	createError,
	isPending,
	onSituationChange,
	onExpectedBehaviorChange,
	onAdvancedJsonChange,
	onClearFinding,
	onCreate,
}: {
	finding?: Finding;
	situation: string;
	expectedBehavior: string;
	advancedJson: string;
	advancedJsonError: string;
	createError: string;
	isPending: boolean;
	onSituationChange: (value: string) => void;
	onExpectedBehaviorChange: (value: string) => void;
	onAdvancedJsonChange: (value: string) => void;
	onClearFinding: () => void;
	onCreate: () => void;
}) {
	const canCreate = situation.trim() && expectedBehavior.trim();
	return (
		<div className="rounded-xl border bg-card p-4 shadow-sm">
			<h2 className="font-display text-lg font-semibold">
				Improve agent
			</h2>
			{finding && (
				<div className="mt-3 rounded-lg border bg-muted/40 p-3">
					<div className="flex items-center justify-between gap-3">
						<p className="text-sm font-medium">
							Drafting from finding
						</p>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							onClick={onClearFinding}
						>
							Clear Finding
						</Button>
					</div>
					<p className="mt-2 text-sm text-muted-foreground">
						{finding.description}
					</p>
				</div>
			)}
			<div className="mt-4 space-y-3">
				<div className="space-y-2">
					<Label htmlFor="test-situation">Situation</Label>
					<Textarea
						id="test-situation"
						value={situation}
						onChange={(event) =>
							onSituationChange(event.target.value)
						}
						placeholder="When the user asks…"
					/>
				</div>
				<div className="space-y-2">
					<Label htmlFor="test-expected">Expected behavior</Label>
					<Textarea
						id="test-expected"
						value={expectedBehavior}
						onChange={(event) =>
							onExpectedBehaviorChange(event.target.value)
						}
						placeholder="The agent should…"
					/>
				</div>
				<details className="rounded-lg border p-3">
					<summary className="cursor-pointer text-sm font-medium">
						Advanced JSON/checks
					</summary>
					<Label className="mt-3 block" htmlFor="test-advanced-json">
						Advanced JSON
					</Label>
					<Textarea
						id="test-advanced-json"
						className="mt-2 font-mono text-xs"
						value={advancedJson}
						onChange={(event) =>
							onAdvancedJsonChange(event.target.value)
						}
						placeholder='{"forbidden_tools":["..."]}'
					/>
					{advancedJsonError && (
						<p className="mt-2 text-sm text-destructive">
							{advancedJsonError}
						</p>
					)}
				</details>
				{createError && (
					<p className="text-sm text-destructive" role="alert">
						{createError}
					</p>
				)}
				<Button
					type="button"
					onClick={onCreate}
					disabled={!canCreate || isPending}
				>
					Create Test
				</Button>
			</div>
		</div>
	);
}

function Inspector({
	collection,
	item,
	agentId,
	latestByLogicalId,
	onCreateTestFromFinding,
}: {
	collection: Collection;
	item: AgentTest | Finding | Review | QualityRun | null;
	agentId?: string;
	latestByLogicalId: Map<string, AgentTestLatest>;
	onCreateTestFromFinding?: (finding: Finding) => void;
}) {
	if (!item) {
		return (
			<div className="space-y-2">
				<h2 className="font-display text-lg font-semibold">
					Inspector
				</h2>
				<p className="mt-2 text-sm text-muted-foreground">
					Select an item to inspect its context.
				</p>
			</div>
		);
	}
	return (
		<div className="space-y-4">
			{collection === "tests" && (
				<TestInspector
					test={item as AgentTest}
					agentId={agentId}
					latest={latestByLogicalId.get(
						(item as AgentTest).logical_test_id,
					)}
				/>
			)}
			{collection === "findings" && (
				<FindingInspector
					finding={item as Finding}
					agentId={agentId}
					onCreateTest={
						onCreateTestFromFinding
							? () => onCreateTestFromFinding(item as Finding)
							: undefined
					}
				/>
			)}
			{collection === "reviews" && (
				<ReviewInspector review={item as Review} />
			)}
			{collection === "runs" && (
				<RunInspector run={item as QualityRun} agentId={agentId} />
			)}
		</div>
	);
}

function TestInspector({
	test,
	agentId,
	latest,
}: {
	test: AgentTest;
	agentId?: string;
	latest?: AgentTestLatest;
}) {
	return (
		<div className="space-y-3">
			<h2 className="font-display text-lg font-semibold">{test.name}</h2>
			<p className="text-sm text-muted-foreground">
				Case {test.case_id} · version {test.version}
			</p>
			<pre className="max-h-80 overflow-auto rounded-lg bg-muted p-3 text-xs">
				{JSON.stringify(test.input, null, 2)}
			</pre>
			{latest?.simulation && (
				<p className="text-sm">
					Simulation: {latest.simulation.status} · execution{" "}
					{latest.simulation.execution_id}
				</p>
			)}
			{latest?.recorded && (
				<p className="text-sm">
					Recorded: {latest.recorded.outcome}{" "}
					{agentId && latest.recorded.run_id && (
						<Link
							className="underline"
							to={`/agents/${agentId}/runs/${latest.recorded.run_id}`}
						>
							Run {latest.recorded.run_id}
						</Link>
					)}
				</p>
			)}
		</div>
	);
}

function FindingInspector({
	finding,
	agentId,
	onCreateTest,
}: {
	finding: Finding;
	agentId?: string;
	onCreateTest?: () => void;
}) {
	const runHref =
		agentId && finding.source_run_id
			? `/agents/${agentId}/runs/${finding.source_run_id}?tab=activity${
					finding.source_sequence
						? `&sequence=${finding.source_sequence}`
						: ""
				}`
			: "";
	return (
		<div className="space-y-3">
			<h2 className="font-display text-lg font-semibold">
				Finding details
			</h2>
			<p>{finding.description}</p>
			{finding.expected_behavior && (
				<p className="text-sm text-muted-foreground">
					Expected: {finding.expected_behavior}
				</p>
			)}
			{runHref && (
				<Link className="text-sm underline" to={runHref}>
					Run {finding.source_run_id}
				</Link>
			)}
			{finding.linked_case_ids?.length ? (
				<p className="text-sm text-muted-foreground">
					Linked tests: {finding.linked_case_ids.join(", ")}
				</p>
			) : null}
			{onCreateTest ? (
				<Button type="button" onClick={onCreateTest}>
					Create Test
				</Button>
			) : (
				<p className="text-sm text-muted-foreground">
					Choose this agent in the filter to create a test from this
					finding.
				</p>
			)}
		</div>
	);
}

function ReviewInspector({ review }: { review: Review }) {
	return (
		<div className="space-y-3">
			<h2 className="font-display text-lg font-semibold">
				Review details
			</h2>
			<p>{review.name}</p>
			<p className="text-sm text-muted-foreground">
				Version {review.latest_version} · {review.status}
			</p>
		</div>
	);
}

function RunInspector({ run, agentId }: { run: QualityRun; agentId?: string }) {
	return (
		<div className="space-y-3">
			<h2 className="font-display text-lg font-semibold">Run details</h2>
			<p>{run.asked ?? run.trigger_type}</p>
			{run.did && (
				<p className="text-sm text-muted-foreground">Did: {run.did}</p>
			)}
			{agentId && (
				<Link
					className="text-sm underline"
					to={`/agents/${agentId}/runs/${run.id}`}
				>
					Open Run
				</Link>
			)}
		</div>
	);
}

function RecordedResultsPanel({
	results,
	usage,
	isLoading,
	isError,
	onRetry,
}: {
	results?: RecordedResultsPage;
	usage?: QualityUsage;
	isLoading: boolean;
	isError: boolean;
	onRetry: () => void;
}) {
	if (isLoading) {
		return <CollectionState>Loading recorded results…</CollectionState>;
	}
	if (isError) {
		return (
			<CollectionState
				tone="error"
				actionLabel="Retry recorded results"
				onAction={onRetry}
			>
				Could not load recorded results.
			</CollectionState>
		);
	}
	const recordedResults = results?.results ?? [];
	const missingCost = usage?.coverage.missing_cost_call_count ?? 0;
	const totalCost = usage?.overall.known_cost;
	const costLabel =
		totalCost == null
			? "Cost: Unknown"
			: missingCost > 0
				? `Known cost: ${formatCost(totalCost)}`
				: `Cost: ${formatCost(totalCost)}`;
	return (
		<div className="rounded-xl border bg-card p-4 shadow-sm">
			<h2 className="font-display text-lg font-semibold">
				Recorded results
			</h2>
			<p className="mt-2 text-sm text-muted-foreground">
				<span>{results?.total ?? 0} results</span> ·{" "}
				<span>{costLabel}</span> · Unknown-cost calls: {missingCost}
			</p>
			{recordedResults.length === 0 ? (
				<p className="mt-4 text-sm text-muted-foreground">
					No recorded results have been admitted yet.
				</p>
			) : (
				<div className="mt-4 space-y-3">
					{recordedResults.map((result) => (
						<div
							key={result.id}
							className="rounded-lg border bg-muted/30 p-3"
						>
							<div className="flex flex-wrap items-center gap-2">
								<Badge
									variant={
										result.outcome === "passed"
											? "secondary"
											: "destructive"
									}
								>
									{result.outcome}
								</Badge>
								<Badge variant="outline">
									{result.complete
										? "complete"
										: "incomplete"}
								</Badge>
								<Badge variant="outline">
									{result.applicability}
								</Badge>
							</div>
							<p className="mt-2 text-sm text-muted-foreground">
								Case {result.case_id} v{result.case_version} ·
								Run {result.run_id}
							</p>
							{result.limitations.length > 0 && (
								<ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
									{result.limitations.map((limitation) => (
										<li key={limitation}>{limitation}</li>
									))}
								</ul>
							)}
							{result.error && (
								<p className="mt-2 text-sm text-destructive">
									{result.error}
								</p>
							)}
						</div>
					))}
				</div>
			)}
		</div>
	);
}

function CollectionState({
	children,
	tone = "default",
	actionLabel,
	onAction,
}: {
	children: ReactNode;
	tone?: "default" | "error";
	actionLabel?: string;
	onAction?: () => void;
}) {
	return (
		<div
			className={`rounded-xl border bg-card p-4 text-sm shadow-sm ${
				tone === "error" ? "text-destructive" : "text-muted-foreground"
			}`}
		>
			<div className="flex flex-wrap items-center justify-between gap-3">
				<span>{children}</span>
				{actionLabel && onAction && (
					<Button
						type="button"
						variant="outline"
						size="sm"
						onClick={onAction}
					>
						{actionLabel}
					</Button>
				)}
			</div>
		</div>
	);
}

function FleetAgentFilter({
	agents,
	collection,
	value,
	onValueChange,
}: {
	agents: FleetAgentSummary[];
	collection: Collection;
	value: string;
	onValueChange: (value: string) => void;
}) {
	return (
		<Select value={value || "all"} onValueChange={onValueChange}>
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
	);
}

type FleetCollection = "findings" | "tests" | "reviews";
type FleetAgentSummary = { id: string; name?: string | null };

const FLEET_COLLECTIONS: FleetCollection[] = ["findings", "tests", "reviews"];

function fleetCollectionFromParams(value: string | null): FleetCollection {
	return FLEET_COLLECTIONS.includes(value as FleetCollection)
		? (value as FleetCollection)
		: "findings";
}

export default AgentQualityWorkbench;
