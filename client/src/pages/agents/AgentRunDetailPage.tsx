import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
/**
 * AgentRunDetailPage — full-page detail view for a single agent run.
 *
 * Routes:
 *   /agents/:agentId/runs/:runId  → this page
 *
 * Overview retains asked/did/answered and run-level usage. Activity owns a
 * bounded call hierarchy and an attached desktop inspector; mobile uses a Sheet.
 */

import { useAgentRunStepStore } from "@/stores/agentRunStepStore";
import { AgentActivityWorkspace } from "./AgentActivityWorkspace";
import { AgentRunOverviewFooter } from "./AgentRunOverviewFooter";
import { copyToClipboard } from "@/lib/clipboard";
import { RunActionFeedback } from "./RunActionFeedback";
import { FleetReadError } from "./FleetReadError";
import {
	useEffect,
	useMemo,
	useRef,
	useState,
	type MouseEvent as ReactMouseEvent,
} from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import {
	AlertCircle,
	ArrowLeft,
	CheckCircle,
	Copy,
	Clock,
	Loader2,
	RefreshCw,
	Sparkles,
	XCircle,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { RunDetailHeading } from "@/components/execution/RunDetailHeading";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/contexts/AuthContext";
import { useAgent } from "@/hooks/useAgents";
import { useAgentRunUpdates } from "@/hooks/useAgentRunUpdates";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import {
	createAgentRunNavigationState,
	getLocationHref,
	readAgentRunNavigationOrigin,
	type AgentRunNavigationOrigin,
} from "@/lib/agent-run-navigation";
import { cn, formatDuration } from "@/lib/utils";
import {
	useAgentRun,
	useAgentRunStream,
	useClearVerdict,
	useFlagConversation,
	useRerunAgentRun,
	useSendFlagMessage,
	useSetVerdict,
} from "@/services/agentRuns";
import type { components } from "@/lib/v1";

import { FlagConversation } from "@/components/agents/FlagConversation";
import {
	RunReviewPanel,
	type Verdict,
} from "@/components/agents/RunReviewPanel";
import { activityDomId } from "@/components/agents/run-activity";

type AgentRunDetailResponse = components["schemas"]["AgentRunDetailResponse"];

export function AgentRunDetailPage() {
	const { agentId, runId } = useParams<{
		agentId: string;
		runId: string;
	}>();
	const queryClient = useQueryClient();
	const { isPlatformAdmin } = useAuth();
	const location = useLocation();
	const navigate = useNavigate();
	const activeTab =
		new URLSearchParams(location.search).get("tab") === "activity"
			? "activity"
			: "overview";
	const pendingActivity = useRef<string | null>(null);
	const activityHeaderRef = useRef<HTMLDivElement>(null);
	const activityFrameRef = useRef<HTMLDivElement>(null);
	const pageScrollRef = useRef<HTMLDivElement>(null);
	const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
	const desktopActivity = useMediaQuery("(min-width: 1024px)");
	const [activityInspecting, setActivityInspecting] = useState(false);
	const [activityHeight, setActivityHeight] = useState<number | null>(null);
	function changeTab(tab: string) {
		const params = new URLSearchParams(location.search);
		if (tab === "overview") params.delete("tab");
		else params.set("tab", tab);
		navigate(
			{ pathname: location.pathname, search: params.toString() },
			{ replace: true, state: location.state },
		);
	}
	useEffect(() => {
		if (activeTab !== "activity" || !pendingActivity.current) return;
		const target = document.getElementById(
			activityDomId(pendingActivity.current),
		);
		if (!target) return;
		target.scrollIntoView({ behavior: "auto", block: "center" });
		target.focus({ preventScroll: true });
		pendingActivity.current = null;
	}, [activeTab]);
	useEffect(() => {
		if (!activityInspecting || activeTab === "activity" || !desktopActivity)
			return;
		const header = activityHeaderRef.current;
		if (!header) return;
		let innerFrame = 0;
		const outerFrame = window.requestAnimationFrame(() => {
			innerFrame = window.requestAnimationFrame(() => {
				header.scrollIntoView({
					behavior: reducedMotion ? "auto" : "smooth",
					block: "start",
				});
			});
		});
		return () => {
			window.cancelAnimationFrame(outerFrame);
			if (innerFrame) window.cancelAnimationFrame(innerFrame);
		};
	}, [activeTab, activityInspecting, reducedMotion, desktopActivity]);
	useEffect(() => {
		// Reduced motion has no transition-end event: reveal the header after
		// the final measured height has committed, not against the old scroll range.
		if (
			!reducedMotion ||
			!desktopActivity ||
			!activityInspecting ||
			activeTab === "activity"
		)
			return;
		const frame = requestAnimationFrame(() =>
			activityHeaderRef.current?.scrollIntoView({
				behavior: "auto",
				block: "start",
			}),
		);
		return () => cancelAnimationFrame(frame);
	}, [
		activityHeight,
		reducedMotion,
		desktopActivity,
		activityInspecting,
		activeTab,
	]);
	const navigationOrigin = readAgentRunNavigationOrigin(location.state);

	// `useAgentRun` returns a hand-rolled `AgentRunDetail` type that predates
	// some OpenAPI fields (asked/did/verdict/etc). Re-cast to the OpenAPI
	// schema for full field access.
	const {
		data: rawRun,
		isLoading,
		isError: runError,
		isFetching: runFetching,
		refetch: refetchRun,
	} = useAgentRun(runId);
	useAgentRunStream(runId);
	const streamedSteps = useAgentRunStepStore((state) =>
		runId ? state.streams[runId]?.steps : undefined,
	);
	const run = useMemo(() => {
		const fetched = rawRun as unknown as AgentRunDetailResponse | undefined;
		if (!fetched || !streamedSteps?.length) return fetched;
		const steps = new Map(
			[...streamedSteps, ...(fetched.steps ?? [])].map((step) => [
				step.id,
				step,
			]),
		);
		return {
			...fetched,
			steps: [...steps.values()].sort(
				(a, b) => a.step_number - b.step_number,
			),
		} as AgentRunDetailResponse;
	}, [rawRun, streamedSteps]);
	const owningAgentId = run?.agent_id ?? agentId;
	const {
		data: agent,
		isError: agentError,
		isFetching: agentFetching,
		refetch: refetchAgent,
	} = useAgent(owningAgentId);
	const parentRunId = navigationOrigin
		? undefined
		: (run?.parent_run_id ?? undefined);
	const {
		data: rawParentRun,
		isError: parentError,
		isFetching: parentFetching,
		refetch: refetchParent,
	} = useAgentRun(parentRunId);
	const parentRun = parentRunId
		? (rawParentRun as unknown as AgentRunDetailResponse | undefined)
		: undefined;

	useEffect(() => {
		let animationFrame = 0;
		let settleFrame = 0;
		const frame = activityFrameRef.current;

		function scheduleHeightUpdate() {
			window.cancelAnimationFrame(animationFrame);
			window.cancelAnimationFrame(settleFrame);
			animationFrame = window.requestAnimationFrame(() => {
				if (activeTab === "activity" || !desktopActivity || !frame) {
					setActivityHeight(null);
					return;
				}
				const toolbar = frame.querySelector<HTMLElement>(
					'[data-slot="activity-toolbar"]',
				);
				const activityList = frame.querySelector<HTMLElement>(
					'ol[aria-label="Run activity"]',
				);
				const emptyState = frame.querySelector<HTMLElement>(
					'[data-slot="activity-empty-state"]',
				);
				const advancedContent = frame.querySelector<HTMLElement>(
					'[data-slot="activity-advanced-content"]',
				);
				const naturalHeight = advancedContent
					? advancedContent.offsetHeight + 2
					: (toolbar?.offsetHeight ?? 0) +
						Math.max(
							activityList?.scrollHeight ?? 0,
							emptyState?.offsetHeight ?? 0,
						) +
						2;
				const availableHeight =
					(pageScrollRef.current?.clientHeight ??
						window.innerHeight) - 16;
				const viewportCap = Math.max(availableHeight, 1);
				const target = activityInspecting
					? Math.min(760, viewportCap)
					: Math.min(naturalHeight, 520, viewportCap);
				const current = frame.offsetHeight || target;
				setActivityHeight(current);
				settleFrame = window.requestAnimationFrame(() => {
					setActivityHeight(target);
				});
			});
		}

		scheduleHeightUpdate();
		if (!frame) {
			return () => {
				window.cancelAnimationFrame(animationFrame);
				window.cancelAnimationFrame(settleFrame);
			};
		}
		const resizeObserver =
			typeof ResizeObserver === "undefined"
				? null
				: new ResizeObserver(scheduleHeightUpdate);
		function observeIntrinsicContent() {
			const toolbar = frame?.querySelector<HTMLElement>(
				'[data-slot="activity-toolbar"]',
			);
			const activityList = frame?.querySelector<HTMLElement>(
				'ol[aria-label="Run activity"]',
			);
			const emptyState = frame?.querySelector<HTMLElement>(
				'[data-slot="activity-empty-state"]',
			);
			if (toolbar) resizeObserver?.observe(toolbar);
			if (activityList) resizeObserver?.observe(activityList);
			if (emptyState) resizeObserver?.observe(emptyState);
			const advancedContent = frame?.querySelector<HTMLElement>(
				'[data-slot="activity-advanced-content"]',
			);
			if (advancedContent) resizeObserver?.observe(advancedContent);
		}
		observeIntrinsicContent();
		if (pageScrollRef.current)
			resizeObserver?.observe(pageScrollRef.current);
		const mutationObserver =
			typeof MutationObserver === "undefined"
				? null
				: new MutationObserver(() => {
						resizeObserver?.disconnect();
						observeIntrinsicContent();
						if (pageScrollRef.current)
							resizeObserver?.observe(pageScrollRef.current);
						scheduleHeightUpdate();
					});
		mutationObserver?.observe(frame, { childList: true, subtree: true });
		window.addEventListener("resize", scheduleHeightUpdate);
		return () => {
			mutationObserver?.disconnect();
			resizeObserver?.disconnect();
			window.removeEventListener("resize", scheduleHeightUpdate);
			window.cancelAnimationFrame(animationFrame);
			window.cancelAnimationFrame(settleFrame);
		};
	}, [
		activeTab,
		activityInspecting,
		desktopActivity,
		run?.id,
		run?.steps?.length,
	]);

	// Refetch this run whenever the backend broadcasts an update for it —
	// covers summarizer transitions (pending → generating → completed) and
	// step-writes so the page reflects live state without a manual refresh.
	useAgentRunUpdates({ agentId: owningAgentId });

	const verdict = ((run?.verdict as Verdict | undefined) ?? null) as Verdict;
	const [noteDrafts, setNoteDrafts] = useState<ReadonlyMap<string, string>>(() => new Map());
	const note = runId ? (noteDrafts.get(runId) ?? run?.verdict_note ?? "") : "";
	const setNote = (value: string) => {
		if (runId)
			setNoteDrafts((previous) => new Map(previous).set(runId, value));
	};
	const verdictBusy = useRef(false);
	const [savingVerdict, setSavingVerdict] = useState(false);
	const [verdictFailure, setVerdictFailure] = useState<{
		runId: string;
		verdict: Verdict;
	} | null>(null);
	const [previewedActivityId, setPreviewedActivityId] = useState<
		string | null
	>(null);
	const [expandedDelegationsByRun, setExpandedDelegationsByRun] = useState<
		ReadonlyMap<string, ReadonlySet<string>>
	>(() => {
		const saved = location.state?.runActivity;
		return saved?.runId === runId && Array.isArray(saved.expanded)
			? new Map([
					[
						runId!,
						new Set<string>(
							saved.expanded.filter(
								(id: unknown): id is string =>
									typeof id === "string",
							),
						),
					],
				])
			: new Map();
	});
	const [restoreActivityByRun, setRestoreActivityByRun] = useState<
		ReadonlyMap<string, string>
	>(() => {
		const saved = location.state?.runActivity;
		return saved?.runId === runId && typeof saved.restore === "string"
			? new Map([[runId!, saved.restore]])
			: new Map();
	});

	const setVerdict = useSetVerdict();
	const clearVerdict = useClearVerdict();
	const rerun = useRerunAgentRun();
	const rerunBusy = useRef(false);
	const [rerunFailure, setRerunFailure] = useState<string | null>(null);
	const currentRunOrigin: AgentRunNavigationOrigin | null = run
		? {
				href: getLocationHref(location),
				label: `Back to ${run.agent_name ?? agent?.name ?? "parent"} run`,
			}
		: null;

	const isFlagged = verdict === "down";
	const {
		data: conversation,
		isLoading: conversationLoading,
		isError: conversationError,
		isFetching: conversationFetching,
		refetch: refetchConversation,
	} = useFlagConversation(isFlagged ? runId : undefined);
	const sendMessage = useSendFlagMessage();

	function invalidateRun() {
		queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
		queryClient.invalidateQueries({ queryKey: ["agent-runs", runId] });
	}

	function handleVerdict(next: Verdict) {
		if (!runId || verdictBusy.current) return;
		verdictBusy.current = true;
		setSavingVerdict(true);
		setVerdictFailure(null);
		const onSuccess = () => {
			setNoteDrafts((previous) =>
				new Map(previous).set(runId, next === null ? "" : note),
			);
			invalidateRun();
			void queryClient.invalidateQueries({
				queryKey: ["agent-runs-infinite"],
			});
		};
		const onError = () => setVerdictFailure({ runId, verdict: next });
		const onSettled = () => {
			verdictBusy.current = false;
			setSavingVerdict(false);
		};
		if (next === null)
			clearVerdict.mutate(
				{ params: { path: { run_id: runId } } },
				{ onSuccess, onError, onSettled },
			);
		else
			setVerdict.mutate(
				{
					params: { path: { run_id: runId } },
					body: { verdict: next, note },
				},
				{ onSuccess, onError, onSettled },
			);
	}

	function handleSendChat(text: string): Promise<void> {
		return new Promise((resolve, reject) => {
			if (!runId) {
				reject(new Error("Run unavailable"));
				return;
			}
			sendMessage.mutate(
				{
					params: { path: { run_id: runId } },
					body: { content: text },
				},
				{
					onSuccess: (data) => {
						queryClient.setQueryData(
							[
								"get",
								"/api/agent-runs/{run_id}/flag-conversation",
								{ params: { path: { run_id: runId } } },
							],
							data,
						);
						resolve();
					},
					onError: reject,
				},
			);
		});
	}

	function handleRerun() {
		if (!runId || !run?.agent_id || rerunBusy.current) return;
		rerunBusy.current = true;
		setRerunFailure(null);
		rerun.mutate(
			{ params: { path: { run_id: runId } } },
			{
				onSuccess: (data) => {
					toast.success("Rerun queued");
					if (data.run_id && currentRunOrigin) {
						navigate(
							`/agents/${run.agent_id}/runs/${data.run_id}`,
							{
								state: createAgentRunNavigationState(
									currentRunOrigin,
								),
							},
						);
					}
				},
				onError: () => setRerunFailure(runId),
				onSettled: () => {
					rerunBusy.current = false;
				},
			},
		);
	}

	function handleContextBackClick(event: ReactMouseEvent<HTMLAnchorElement>) {
		if (
			!navigationOrigin ||
			event.defaultPrevented ||
			event.button !== 0 ||
			event.metaKey ||
			event.ctrlKey ||
			event.shiftKey ||
			event.altKey
		) {
			return;
		}

		event.preventDefault();
		navigate(-1);
	}

	function handleActivityReferenceActivate(activityId: string) {
		setPreviewedActivityId(null);
		pendingActivity.current = activityId;
		changeTab("activity");
	}

	function handleDelegationExpandedChange(
		activityId: string,
		expanded: boolean,
	) {
		if (!runId) return;
		setExpandedDelegationsByRun((current) => {
			const nextForRun = new Set(current.get(runId) ?? []);
			if (expanded) {
				nextForRun.add(activityId);
			} else {
				nextForRun.delete(activityId);
			}
			const next = new Map(current);
			next.set(runId, nextForRun);
			return next;
		});
	}

	function handleOpenChildRun(activityId: string) {
		if (!runId) return;
		navigate(getLocationHref(location), {
			replace: true,
			state: {
				...location.state,
				runActivity: {
					runId,
					expanded: [...(expandedDelegationsByRun.get(runId) ?? [])],
					restore: activityId,
				},
			},
		});
		setPreviewedActivityId(null);
		setRestoreActivityByRun((current) => {
			const next = new Map(current);
			next.set(runId, activityId);
			return next;
		});
	}

	if (isLoading) {
		return (
			<div className="flex flex-col gap-4 w-full max-w-[1100px] mx-auto">
				<Skeleton className="h-6 w-32" />
				<Skeleton className="h-12 w-1/2" />
				<div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
					<div className="lg:col-span-2 space-y-4">
						<Skeleton className="h-64 w-full" />
					</div>
					<div className="space-y-4">
						<Skeleton className="h-48 w-full" />
					</div>
				</div>
			</div>
		);
	}

	if (runError && !run) {
		return (
			<div className="mx-auto flex max-w-7xl flex-col gap-4">
				<Button asChild variant="outline" className="w-fit min-h-11">
					<Link
						to={
							navigationOrigin?.href ??
							(agentId ? `/agents/${agentId}` : "/agents")
						}
						onClick={handleContextBackClick}
					>
						{navigationOrigin?.label ?? "Back to agent"}
					</Link>
				</Button>
				<h1 className="font-display text-2xl font-semibold">
					Agent run
				</h1>
				<FleetReadError
					resource="run details"
					cached={false}
					pending={runFetching}
					onRetry={() => void refetchRun()}
				/>
			</div>
		);
	}

	if (!run) {
		const notFoundBackHref =
			navigationOrigin?.href ??
			(agentId ? `/agents/${agentId}` : "/agents");
		return (
			<div
				className="flex flex-col items-center justify-center gap-3 py-16 text-center"
				data-testid="run-not-found"
			>
				<AlertCircle className="h-10 w-10 text-muted-foreground" />
				<div className="text-lg font-medium">Run not found</div>
				<Button asChild variant="outline">
					<Link
						to={notFoundBackHref}
						onClick={handleContextBackClick}
					>
						{navigationOrigin?.label ?? "Back to agent"}
					</Link>
				</Button>
			</div>
		);
	}

	const summaryStatus =
		(run as unknown as { summary_status?: string }).summary_status ?? null;
	const summaryFailed = summaryStatus === "failed";
	const summaryInFlight =
		summaryStatus === "pending" || summaryStatus === "generating";
	// While the summarizer is running, the RunReviewPanel already shows a
	// status banner with a regenerate button — hide the sidebar card so we
	// don't render two competing affordances.
	const showRegen = isPlatformAdmin && !summaryFailed && !summaryInFlight;
	const headerSummary = run.agent_name || agent?.name || "Agent run";
	const parentRunHref = parentRun?.agent_id
		? `/agents/${parentRun.agent_id}/runs/${parentRun.id}`
		: null;
	const backHref =
		navigationOrigin?.href ??
		parentRunHref ??
		(run.agent_id ? `/agents/${run.agent_id}` : "/agents");
	const backLabel =
		navigationOrigin?.label ??
		(parentRunHref
			? `Back to ${parentRun?.agent_name ?? "parent"} run`
			: run.agent_id
				? `Back to ${agent?.name ?? run.agent_name ?? "agent"}`
				: "Back to agents");

	return (
		<PageWorkspace
			className="flex flex-col gap-4 w-full max-w-[1100px] mx-auto"
			data-testid="agent-run-detail-page"
		>
			<div className="shrink-0 space-y-3">
				{runError ? (
					<FleetReadError
						resource="run details"
						cached
						pending={runFetching}
						onRetry={() => void refetchRun()}
					/>
				) : null}
				{agentError ? (
					<FleetReadError
						resource="agent information"
						cached={!!agent}
						pending={agentFetching}
						onRetry={() => void refetchAgent()}
					/>
				) : null}
				{parentRunId && parentError ? (
					<FleetReadError
						resource="parent run"
						cached={!!parentRun}
						pending={parentFetching}
						onRetry={() => void refetchParent()}
					/>
				) : null}
				{/* Breadcrumb */}
				<Link
					to={backHref}
					onClick={handleContextBackClick}
					data-testid="run-context-back"
					className="inline-flex min-h-11 max-w-full w-fit items-center gap-2 rounded-[var(--bf-radius-control)] text-sm text-muted-foreground [overflow-wrap:anywhere] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
				>
					<ArrowLeft aria-hidden="true" className="size-4 shrink-0" />
					{backLabel}
				</Link>

				<RunDetailHeading
					title={headerSummary}
					metadata={
						<div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
							<RunStatusBadge status={run.status} />
							<span>
								{run.caller_name ??
									run.caller_email ??
									"System"}{" "}
								· {run.trigger_type}
							</span>
							{run.started_at ? (
								<span className="inline-flex items-center gap-1">
									<Clock className="h-3 w-3" />
									{new Date(run.started_at).toLocaleString()}
								</span>
							) : null}
							{run.duration_ms != null ? (
								<>
									<span>·</span>
									<span>
										{formatDuration(run.duration_ms)}
									</span>
								</>
							) : null}
						</div>
					}
					actionsLabel="Agent run actions"
					actions={
						<>
							<Button asChild variant="outline"><Link to={`/agents/${agentId}/runs/${runId}/debug`}>Open debugger</Link></Button>
							<Button
								variant="ghost"
								size="icon"
								aria-label="Copy run ID"
								title="Copy run ID"
								onClick={async () => {
									if (await copyToClipboard(run.id))
										toast.success("Run ID copied");
									else toast.error("Could not copy run ID");
								}}
							>
								<Copy className="size-4" />
							</Button>
							<Button
								type="button"
								variant="outline"
								className="min-h-11 min-w-0"
								data-testid="rerun-button"
								disabled={rerun.isPending || !run.agent_id}
								onClick={handleRerun}
							>
								{rerun.isPending ? (
									<Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
								) : (
									<RefreshCw className="h-3.5 w-3.5" />
								)}
								Rerun
							</Button>
						</>
					}
				/>

				<RunActionFeedback
					pending={rerun.isPending}
					failed={rerunFailure === runId}
					onRetry={handleRerun}
					message="Could not queue a new run. Try again to rerun this execution."
					pendingLabel="Queuing rerun…"
					retryLabel="Retry rerun"
				/>
			</div>

			<PageScrollArea
				ref={pageScrollRef}
				className={
					activeTab === "activity"
						? "flex flex-col lg:min-h-0 lg:flex-1"
						: "space-y-4 lg:min-h-0 lg:flex-1"
				}
			>
				{activeTab !== "activity" ? (
					<div className="space-y-4" data-testid="run-overview">
						<RunActionFeedback
							pending={savingVerdict}
							failed={verdictFailure?.runId === runId}
							onRetry={() => {
								if (verdictFailure)
									handleVerdict(verdictFailure.verdict);
							}}
						/>
						<fieldset
							disabled={savingVerdict}
							className="min-w-0"
							aria-label="Run review"
						>
							<Card className="min-w-0 gap-0 overflow-hidden py-0">
								<RunReviewPanel
									run={run}
									variant="page"
									verdict={verdict}
									note={note}
									onVerdict={handleVerdict}
									onNote={setNote}
									onActivityReferencePreview={
										setPreviewedActivityId
									}
									onActivityReferenceActivate={
										handleActivityReferenceActivate
									}
								/>
								<AgentRunOverviewFooter
									run={run}
									agentName={
										agent?.name ?? run.agent_name ?? null
									}
									showRegen={showRegen}
									isPlatformAdmin={isPlatformAdmin}
								/>
							</Card>
							{verdict && note !== (run.verdict_note ?? "") ? (
								<Button
									className="mt-3"
									onClick={() => handleVerdict(verdict)}
								>
									Save review note
								</Button>
							) : null}
						</fieldset>

						{isFlagged ? (
							<Card data-testid="flag-conversation-card">
								<CardHeader className="pb-2">
									<CardTitle className="flex items-center gap-2 text-sm">
										<Sparkles className="h-4 w-4" />
										Tuning conversation
									</CardTitle>
								</CardHeader>
								<CardContent className="p-0">
									{conversationError ? (
										<div className="px-4 pb-3">
											<FleetReadError
												resource="tuning conversation"
												cached={!!conversation}
												pending={conversationFetching}
												onRetry={() =>
													void refetchConversation()
												}
											/>
										</div>
									) : null}
									{conversationLoading ? (
										<div className="p-4">
											<Skeleton className="h-40 w-full" />
										</div>
									) : conversationError &&
									  !conversation ? null : (
										<div className="flex h-[420px] flex-col">
											<FlagConversation
												conversation={
													conversation ?? null
												}
												onSend={handleSendChat}
												pending={sendMessage.isPending}
											/>
										</div>
									)}
								</CardContent>
							</Card>
						) : null}
					</div>
				) : null}

				<div
					ref={activityHeaderRef}
					data-activity-inspecting={activityInspecting}
					className={cn(
						"flex min-w-0 flex-col",
						activeTab === "activity"
							? "lg:min-h-0 lg:flex-1"
							: "lg:overflow-hidden lg:transition-[height] lg:duration-300 lg:ease-out motion-reduce:lg:transition-none",
					)}
					onTransitionEnd={(event) => {
						if (
							event.propertyName !== "height" ||
							event.target !== event.currentTarget ||
							!activityInspecting ||
							activeTab === "activity"
						) {
							return;
						}
						activityHeaderRef.current?.scrollIntoView({
							behavior: reducedMotion ? "auto" : "smooth",
							block: "start",
						});
					}}
					style={
						activeTab === "activity" ||
						!desktopActivity ||
						activityHeight == null
							? undefined
							: {
									height: activityHeight,
								}
					}
				>
					<Card
						ref={activityFrameRef}
						className={
							activeTab === "activity" || activityInspecting
								? "flex min-h-0 min-w-0 flex-1 flex-col gap-0 overflow-hidden py-0"
								: "flex min-h-0 min-w-0 flex-col gap-0 overflow-hidden py-0"
						}
					>
						<CardContent className="flex min-h-0 min-w-0 flex-1 flex-col p-0">
							<AgentActivityWorkspace
								run={run}
								focused={activeTab === "activity"}
								expanded={activityInspecting}
								onFocusedChange={(focused) =>
									changeTab(focused ? "activity" : "overview")
								}
								onInspectionChange={setActivityInspecting}
								childRunIds={run.child_run_ids ?? []}
								childRuns={run.child_runs ?? []}
								runStatus={run.status}
								highlightedActivityId={previewedActivityId}
								expandedDelegationIds={
									runId
										? expandedDelegationsByRun.get(runId)
										: undefined
								}
								onDelegationExpandedChange={
									handleDelegationExpandedChange
								}
								restoreActivityId={
									runId
										? restoreActivityByRun.get(runId)
										: null
								}
								onOpenChildRun={handleOpenChildRun}
								childRunOrigin={currentRunOrigin ?? undefined}
							/>
						</CardContent>
					</Card>
				</div>
			</PageScrollArea>
		</PageWorkspace>
	);
}

function RunStatusBadge({ status }: { status: string }) {
	switch (status) {
		case "completed":
			return (
				<Badge
					variant="outline"
					className="border-[color:var(--bf-success)]/30 bg-[color:var(--bf-success)]/10 text-[color:var(--bf-success)]"
				>
					<CheckCircle className="h-3 w-3" /> Completed
				</Badge>
			);
		case "failed":
			return (
				<Badge
					variant="outline"
					className="border-[color:var(--bf-danger)]/30 bg-[color:var(--bf-danger)]/10 text-[color:var(--bf-danger)]"
				>
					<XCircle className="h-3 w-3" /> Failed
				</Badge>
			);
		case "running":
			return (
				<Badge
					variant="outline"
					className="border-primary/30 bg-primary/10 text-primary"
				>
					<Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />{" "}
					Running
				</Badge>
			);
		case "budget_exceeded":
			return (
				<Badge
					variant="outline"
					className="border-[color:var(--bf-warning)]/30 bg-[color:var(--bf-warning)]/10 text-[color:var(--bf-warning)]"
				>
					<AlertCircle className="h-3 w-3" /> Budget exceeded
				</Badge>
			);
		default:
			return <Badge variant="outline">{status}</Badge>;
	}
}
