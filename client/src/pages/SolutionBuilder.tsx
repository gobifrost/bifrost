/**
 * Private Solution Builder workspace.
 *
 * Layout follows the builder design spec:
 *   left   - builder chat (sessions, messages, composer, turn status)
 *   right  - Preview / Code / Changes workbench
 *
 * The chat surface composes the existing chat components (ChatWindow drives
 * itself from a conversation id, which a builder session supplies), so builder
 * state and APIs stay separate from ordinary conversations.
 */

import {
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
	type CSSProperties,
	type PointerEvent as ReactPointerEvent,
} from "react";
import { useLocation, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Code2,
	Download,
	Eye,
	ExternalLink,
	FileDiff,
	Loader2,
	Lock,
	MessageSquarePlus,
	Send,
	Sparkles,
	Undo2,
} from "lucide-react";
import { ChatWindow } from "@/components/chat";
import { BuilderChangesPanel } from "@/components/builder/BuilderChangesPanel";
import { BuilderCodePanel } from "@/components/builder/BuilderCodePanel";
import {
	PreviewPane,
	type PreviewDevice,
} from "@/components/builder/PreviewPane";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	BuilderApiError,
	createBuilderAppLaunch,
	createBuilderSession,
	currentRevision,
	deployedRevision,
	downloadRevision,
	getBuilderSolution,
	isPreviewStale,
	latestTurn,
	listBuilderSessions,
	listRevisions,
	listTurns,
	requestPromotion,
	runBuilderTurn,
	undoToRevision,
	type BuilderTurnStatus,
} from "@/services/builder";
import { useApplications } from "@/hooks/useApplications";
import {
	loadBuilderWorkbenchState,
	saveBuilderWorkbenchState,
	type BuilderMobilePane,
	type BuilderWorkbenchTab,
} from "@/lib/builder-workbench-state";
import { cn } from "@/lib/utils";

/** Poll while a turn is in flight so build status stays live. */
const ACTIVE_POLL_MS = 3000;

function turnBadgeVariant(
	status: BuilderTurnStatus,
): "default" | "secondary" | "destructive" | "outline" {
	if (status === "failed") return "destructive";
	if (status === "succeeded") return "secondary";
	return "default";
}

function turnLabel(status: BuilderTurnStatus | null): string {
	if (!status) return "Agent ready";
	switch (status) {
		case "queued":
			return "Queued";
		case "running":
			return "Building app";
		case "succeeded":
			return "Succeeded";
		case "failed":
			return "Build failed";
	}
}

function triggerDownload(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = filename;
	anchor.click();
	URL.revokeObjectURL(url);
}

export function SolutionBuilder() {
	const { solutionId } = useParams<{ solutionId: string }>();
	const location = useLocation();
	const queryClient = useQueryClient();
	const initialPromptHandled = useRef(false);
	const id = solutionId ?? "";
	const locationState = location.state as {
		initialPrompt?: unknown;
		initialSessionId?: unknown;
	} | null;
	const initialPrompt =
		typeof locationState?.initialPrompt === "string"
			? locationState.initialPrompt.trim()
			: "";
	const initialSessionId =
		typeof locationState?.initialSessionId === "string"
			? locationState.initialSessionId
			: null;
	const [restoredWorkbench] = useState(() => loadBuilderWorkbenchState(id));

	const [workbenchTab, setWorkbenchTab] = useState<BuilderWorkbenchTab>(
		restoredWorkbench.workbenchTab,
	);
	const [mobilePane, setMobilePane] = useState<BuilderMobilePane>(
		restoredWorkbench.mobilePane,
	);
	const [agentPanelWidth, setAgentPanelWidth] = useState(
		restoredWorkbench.agentPanelWidth,
	);
	const workbenchRef = useRef<HTMLDivElement>(null);
	const [previewRoute, setPreviewRoute] = useState(
		restoredWorkbench.previewRoute,
	);
	const [previewDevice, setPreviewDevice] = useState<PreviewDevice>(
		restoredWorkbench.previewDevice,
	);
	const [previewNonce, setPreviewNonce] = useState(0);
	const [activeSessionId, setActiveSessionId] = useState<string | null>(
		initialSessionId ?? restoredWorkbench.activeSessionId,
	);
	const [actionError, setActionError] = useState<string | null>(null);

	const solutionQuery = useQuery({
		queryKey: ["builder", "solution", id],
		queryFn: ({ signal }) => getBuilderSolution(id, { signal }),
		enabled: Boolean(id),
		retry: false,
	});

	const sessionsQuery = useQuery({
		queryKey: ["builder", "sessions", id],
		queryFn: ({ signal }) => listBuilderSessions(id, { signal }),
		enabled: Boolean(id),
	});

	const revisionsQuery = useQuery({
		queryKey: ["builder", "revisions", id],
		queryFn: ({ signal }) => listRevisions(id, { signal }),
		enabled: Boolean(id),
	});

	const turnsQuery = useQuery({
		queryKey: ["builder", "turns", id],
		queryFn: ({ signal }) => listTurns(id, { signal }),
		enabled: Boolean(id),
		refetchInterval: (query) => {
			const turn = latestTurn(query.state.data ?? []);
			return turn &&
				(turn.status === "queued" || turn.status === "running")
				? ACTIVE_POLL_MS
				: false;
		},
	});
	const applicationsQuery = useApplications();

	const revisions = useMemo(
		() => revisionsQuery.data ?? [],
		[revisionsQuery.data],
	);
	const sessions = useMemo(
		() => sessionsQuery.data ?? [],
		[sessionsQuery.data],
	);
	const turn = latestTurn(turnsQuery.data ?? []);
	const stale = isPreviewStale(revisions);
	const source = currentRevision(revisions);
	const deployed = deployedRevision(revisions);
	const solution = solutionQuery.data;
	const builderApp = applicationsQuery.data?.applications.find(
		(app) => app.solution_id === id,
	);
	const selectedSession =
		sessions.find((session) => session.id === activeSessionId) ??
		sessions.find((session) => session.id === initialSessionId) ??
		sessions[0];

	const canLaunchApp = Boolean(
		solution?.app_origin && builderApp && deployed,
	);
	const resizeAgentPanel = useCallback(
		(event: ReactPointerEvent<HTMLDivElement>) => {
			const bounds = workbenchRef.current?.getBoundingClientRect();
			if (!bounds) return;
			const { left, width } = bounds;
			event.preventDefault();

			function handlePointerMove(moveEvent: PointerEvent) {
				const percentage = ((moveEvent.clientX - left) / width) * 100;
				setAgentPanelWidth(Math.min(58, Math.max(32, percentage)));
			}

			function stopResize() {
				window.removeEventListener("pointermove", handlePointerMove);
				window.removeEventListener("pointerup", stopResize);
			}

			window.addEventListener("pointermove", handlePointerMove);
			window.addEventListener("pointerup", stopResize);
		},
		[],
	);

	function selectWorkbenchTab(tab: BuilderWorkbenchTab) {
		setWorkbenchTab(tab);
		setMobilePane(tab);
	}

	const previewLaunchQuery = useQuery({
		queryKey: [
			"builder",
			"launch",
			id,
			builderApp?.id ?? null,
			previewRoute,
			previewNonce,
		],
		queryFn: ({ signal }) =>
			createBuilderAppLaunch(id, builderApp!.id, previewRoute, {
				signal,
			}),
		enabled: canLaunchApp,
		retry: false,
		// Launch codes are one-time credentials. Never reuse one after this
		// workspace unmounts and later reopens.
		staleTime: 0,
		gcTime: 0,
		refetchOnMount: "always",
	});

	const invalidateAll = useCallback(() => {
		queryClient.invalidateQueries({
			queryKey: ["builder", "revisions", id],
		});
		queryClient.invalidateQueries({ queryKey: ["builder", "turns", id] });
		queryClient.invalidateQueries({
			queryKey: ["get", "/api/applications"],
		});
	}, [queryClient, id]);

	const newSessionMutation = useMutation({
		mutationFn: () => createBuilderSession(id),
		onSuccess: (session) => {
			queryClient.invalidateQueries({
				queryKey: ["builder", "sessions", id],
			});
			setActiveSessionId(session.id);
		},
		onError: (error: Error) => setActionError(error.message),
	});

	const undoMutation = useMutation({
		mutationFn: (revisionId: string) => {
			if (!selectedSession) {
				throw new Error(
					"Start a builder session before restoring a revision",
				);
			}
			return undoToRevision(id, {
				toRevisionId: revisionId,
				sessionId: selectedSession.id,
			});
		},
		onSuccess: invalidateAll,
		onError: (error: Error) => setActionError(error.message),
	});

	const downloadMutation = useMutation({
		mutationFn: (revisionId: string) => downloadRevision(id, revisionId),
		onSuccess: ({ blob, filename }) => triggerDownload(blob, filename),
		onError: (error: Error) => setActionError(error.message),
	});

	const promotionMutation = useMutation({
		mutationFn: () => requestPromotion(id),
		onSuccess: () =>
			queryClient.invalidateQueries({
				queryKey: ["builder", "solution", id],
			}),
		onError: (error: Error) => setActionError(error.message),
	});

	const runTurnMutation = useMutation({
		mutationFn: (params: { sessionId: string; message: string }) =>
			runBuilderTurn(id, params),
		onMutate: () => setActionError(null),
		onSuccess: () => {
			invalidateAll();
			if (selectedSession) {
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/chat/conversations/{conversation_id}/messages",
					],
				});
			}
		},
		onError: (error: Error) => setActionError(error.message),
	});

	const openAppMutation = useMutation({
		mutationFn: () => {
			if (!builderApp) {
				throw new Error("The app has not deployed yet");
			}
			return createBuilderAppLaunch(id, builderApp.id, previewRoute);
		},
		onSuccess: ({ launch_url }) => {
			window.open(launch_url, "_blank", "noopener,noreferrer");
		},
		onError: (error: Error) => setActionError(error.message),
	});

	const handleBuilderMessage = useCallback(
		(message: string) => {
			if (!selectedSession) {
				setActionError(
					"Start a builder session before sending a message",
				);
				return;
			}
			runTurnMutation.mutate({
				sessionId: selectedSession.id,
				message,
			});
		},
		[runTurnMutation, selectedSession],
	);

	useEffect(() => {
		saveBuilderWorkbenchState(id, {
			activeSessionId,
			workbenchTab,
			mobilePane,
			agentPanelWidth,
			previewRoute,
			previewDevice,
		});
	}, [
		activeSessionId,
		agentPanelWidth,
		id,
		mobilePane,
		previewDevice,
		previewRoute,
		workbenchTab,
	]);

	useEffect(() => {
		if (initialPromptHandled.current || !initialPrompt) {
			return;
		}
		const targetSession = initialSessionId
			? sessions.find((session) => session.id === initialSessionId)
			: selectedSession;
		if (!targetSession) return;
		initialPromptHandled.current = true;
		runTurnMutation.mutate({
			sessionId: targetSession.id,
			message: initialPrompt,
		});
	}, [
		initialPrompt,
		initialSessionId,
		runTurnMutation,
		selectedSession,
		sessions,
	]);

	useEffect(() => {
		if (turn?.status === "succeeded" || turn?.status === "failed") {
			queryClient.invalidateQueries({
				queryKey: ["builder", "revisions", id],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/applications"],
			});
		}
	}, [id, queryClient, turn?.id, turn?.status]);

	const displayedTurnStatus: BuilderTurnStatus | null =
		runTurnMutation.isPending ? "running" : (turn?.status ?? null);
	const promotionReady = Boolean(
		source &&
		deployed &&
		source.id === deployed.id &&
		displayedTurnStatus !== "queued" &&
		displayedTurnStatus !== "running",
	);
	const promotionGuidance = !source
		? "Create source before requesting review"
		: !deployed
			? "Deploy a preview before requesting review"
			: source.id !== deployed.id
				? "Update the preview to match Source"
				: "Ready for review";
	const previewState:
		"unconfigured" | "waiting" | "loading" | "failed" | "ready" =
		!solution?.app_origin
			? "unconfigured"
			: !builderApp || !deployed
				? "waiting"
				: previewLaunchQuery.isError
					? "failed"
					: previewLaunchQuery.data
						? "ready"
						: "loading";

	if (solutionQuery.isLoading) {
		return (
			<div className="space-y-4 p-6" data-testid="builder-loading">
				<Skeleton className="h-10 w-64" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (solutionQuery.isError) {
		const error = solutionQuery.error;
		const notFound = error instanceof BuilderApiError && error.isNotFound;
		return (
			<div
				className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center"
				data-testid="builder-error"
			>
				<h1 className="text-lg font-semibold">
					{notFound ? "App not found" : "Could not open the builder"}
				</h1>
				<p className="max-w-md text-sm text-muted-foreground">
					{notFound
						? "This app does not exist, or it is not yours."
						: (error as Error).message}
				</p>
				{!notFound ? (
					<Button
						variant="outline"
						size="sm"
						onClick={() => solutionQuery.refetch()}
					>
						Try again
					</Button>
				) : null}
			</div>
		);
	}

	const promotionRequested = solution?.promotion_status === "requested";

	return (
		<TooltipProvider>
			<div className="flex h-full min-h-0 flex-col bg-background">
				<header className="flex flex-wrap items-center gap-3 border-b px-3 py-2.5 sm:px-4">
					<div className="min-w-0 flex-1">
						<div className="flex min-w-0 items-center gap-2">
							<h1 className="truncate text-base font-semibold sm:text-lg">
								{solution?.name}
							</h1>
							{solution?.visibility === "private" ? (
								<Badge
									variant="outline"
									className="shrink-0 gap-1"
								>
									<Lock className="h-3 w-3" />
									Private
								</Badge>
							) : null}
						</div>
						<p className="truncate text-xs text-muted-foreground">
							Build / {solution?.slug}
						</p>
					</div>

					<div className="flex w-full items-center gap-1.5 overflow-x-auto sm:w-auto">
						<Button
							variant="ghost"
							size="sm"
							className="h-9 sm:h-7"
							aria-label="Undo latest source change"
							disabled={
								!source?.parent_revision_id ||
								!selectedSession ||
								undoMutation.isPending
							}
							onClick={() => {
								if (source?.parent_revision_id) {
									undoMutation.mutate(
										source.parent_revision_id,
									);
								}
							}}
						>
							<Undo2 className="h-4 w-4" />
							<span className="hidden xl:inline">Undo</span>
						</Button>
						<Button
							variant="ghost"
							size="sm"
							className="h-9 sm:h-7"
							aria-label="Download source"
							disabled={!source || downloadMutation.isPending}
							onClick={() =>
								source && downloadMutation.mutate(source.id)
							}
						>
							{downloadMutation.isPending ? (
								<Loader2 className="h-4 w-4 animate-spin" />
							) : (
								<Download className="h-4 w-4" />
							)}
							<span className="hidden xl:inline">Download</span>
						</Button>
						<Tooltip>
							<TooltipTrigger asChild>
								<span>
									<Button
										variant="outline"
										size="sm"
										className="h-9 sm:h-7"
										aria-label="Open app"
										disabled={
											!canLaunchApp ||
											openAppMutation.isPending
										}
										onClick={() => openAppMutation.mutate()}
									>
										<ExternalLink className="h-4 w-4" />
										<span className="hidden xl:inline">
											Open app
										</span>
									</Button>
								</span>
							</TooltipTrigger>
							{!canLaunchApp ? (
								<TooltipContent>
									{solution?.app_origin
										? "The app needs a successful deploy first"
										: "The separate app host is not configured"}
								</TooltipContent>
							) : null}
						</Tooltip>
						<Tooltip>
							<TooltipTrigger asChild>
								<span>
									<Button
										variant={
											promotionReady &&
											!promotionRequested
												? "default"
												: "outline"
										}
										size="sm"
										className="h-9 sm:h-7"
										aria-label={
											promotionRequested
												? "Promotion requested"
												: promotionReady
													? "Request promotion"
													: promotionGuidance
										}
										disabled={
											promotionRequested ||
											!promotionReady ||
											promotionMutation.isPending
										}
										onClick={() =>
											promotionMutation.mutate()
										}
									>
										<Send className="h-4 w-4" />
										<span className="hidden sm:inline">
											{promotionRequested
												? "Requested"
												: promotionReady
													? "Request promotion"
													: "Preview first"}
										</span>
									</Button>
								</span>
							</TooltipTrigger>
							{!promotionRequested && !promotionReady ? (
								<TooltipContent>
									{promotionGuidance}
								</TooltipContent>
							) : null}
						</Tooltip>
					</div>
				</header>

				<div
					className="flex min-h-9 items-center gap-3 overflow-x-auto border-b bg-muted/25 px-3 py-1.5 text-xs sm:px-4"
					aria-live="polite"
				>
					<Badge
						variant={turnBadgeVariant(
							displayedTurnStatus ?? "succeeded",
						)}
						data-testid="build-status"
					>
						{turnLabel(displayedTurnStatus)}
					</Badge>
					<span className="flex shrink-0 items-center gap-1.5 text-muted-foreground">
						<Sparkles className="h-3.5 w-3.5 text-primary" />
						Skill{" "}
						<strong className="font-medium text-foreground">
							bifrost-build
						</strong>
					</span>
					<span className="shrink-0 text-muted-foreground">
						Source{" "}
						<code className="text-foreground">
							{source?.id.slice(0, 8) ?? "none"}
						</code>
					</span>
					<span aria-hidden="true" className="text-muted-foreground">
						→
					</span>
					<span className="shrink-0 text-muted-foreground">
						Preview{" "}
						<code className="text-foreground">
							{deployed?.id.slice(0, 8) ?? "not deployed"}
						</code>
					</span>
					{stale ? (
						<span
							className="shrink-0 text-amber-600 dark:text-amber-400"
							data-testid="source-ahead-note"
						>
							Preview is behind source
						</span>
					) : null}
					<span
						className={cn(
							"ml-auto flex shrink-0 items-center gap-1.5",
							promotionReady
								? "text-emerald-600 dark:text-emerald-400"
								: "text-muted-foreground",
						)}
					>
						<span
							className={cn(
								"h-1.5 w-1.5 rounded-full",
								promotionReady
									? "bg-emerald-500"
									: "bg-muted-foreground/50",
							)}
						/>
						{promotionRequested
							? "Awaiting admin review"
							: promotionReady
								? "Ready for review"
								: promotionGuidance}
					</span>
				</div>

				{actionError && (
					<p
						className="border-b bg-destructive/10 px-4 py-2 text-sm text-destructive"
						role="alert"
					>
						{actionError}
					</p>
				)}
				{!actionError && turn?.status === "failed" && turn.error && (
					<p
						className="border-b bg-destructive/10 px-4 py-2 text-sm text-destructive"
						role="alert"
					>
						{turn.error}
					</p>
				)}

				<div className="grid grid-cols-4 border-b p-1 lg:hidden">
					{(
						[
							["agent", Sparkles, "Agent"],
							["preview", Eye, "Preview"],
							["code", Code2, "Code"],
							["changes", FileDiff, "Changes"],
						] as const
					).map(([value, Icon, label]) => (
						<Button
							key={value}
							variant={
								mobilePane === value ? "secondary" : "ghost"
							}
							size="sm"
							className="min-h-10 gap-1.5 px-2 text-xs"
							onClick={() => {
								if (value === "agent") {
									setMobilePane("agent");
								} else {
									selectWorkbenchTab(value);
								}
							}}
						>
							<Icon className="h-3.5 w-3.5" />
							{label}
						</Button>
					))}
				</div>

				<div
					ref={workbenchRef}
					className="flex min-h-0 flex-1 flex-col lg:flex-row"
					style={
						{
							"--agent-panel-width": `${agentPanelWidth}%`,
						} as CSSProperties
					}
				>
					<section
						className={cn(
							"min-h-0 flex-1 flex-col border-b lg:w-[var(--agent-panel-width)] lg:flex-none lg:border-b-0 lg:border-r",
							mobilePane === "agent" ? "flex" : "hidden lg:flex",
						)}
					>
						<div className="flex min-h-11 items-center justify-between gap-2 border-b px-3 py-2">
							<div className="min-w-0">
								<div className="flex items-center gap-2 text-sm font-medium">
									Agent
									<Badge
										variant="outline"
										className="gap-1 border-primary/20 text-[10px] text-primary"
									>
										<Sparkles className="h-2.5 w-2.5" />
										bifrost-build
									</Badge>
								</div>
								<p className="truncate text-[11px] text-muted-foreground">
									bifrost-build guides each generated change
								</p>
							</div>
							<Button
								variant="ghost"
								size="sm"
								disabled={newSessionMutation.isPending}
								onClick={() => newSessionMutation.mutate()}
							>
								<MessageSquarePlus className="h-4 w-4" />
								<span className="hidden xl:inline">
									New session
								</span>
							</Button>
						</div>

						{sessions.length > 1 && (
							<div className="flex gap-1 overflow-x-auto border-b px-3 py-2">
								{sessions.map((session, index) => (
									<Button
										key={session.id}
										variant={
											session.id === selectedSession?.id
												? "secondary"
												: "ghost"
										}
										size="sm"
										onClick={() =>
											setActiveSessionId(session.id)
										}
									>
										Session {index + 1}
									</Button>
								))}
							</div>
						)}

						<div className="min-h-0 flex-1">
							{sessionsQuery.isLoading ? (
								<div className="space-y-2 p-4">
									<Skeleton className="h-16 w-full" />
									<Skeleton className="h-16 w-full" />
								</div>
							) : selectedSession ? (
								<ChatWindow
									conversationId={
										selectedSession.conversation_id
									}
									agentName="App Builder"
									onSend={handleBuilderMessage}
									isSending={runTurnMutation.isPending}
									inputPlaceholder="Describe what to build or change…"
								/>
							) : (
								<div
									className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center"
									data-testid="no-session"
								>
									<p className="text-sm text-muted-foreground">
										Start a session to describe what you
										want to build.
									</p>
									<Button
										size="sm"
										disabled={newSessionMutation.isPending}
										onClick={() =>
											newSessionMutation.mutate()
										}
									>
										{newSessionMutation.isPending && (
											<Loader2 className="mr-2 h-4 w-4 animate-spin" />
										)}
										Start session
									</Button>
								</div>
							)}
						</div>
					</section>

					<div
						role="separator"
						aria-label="Resize Agent panel"
						aria-orientation="vertical"
						aria-valuemin={32}
						aria-valuemax={58}
						aria-valuenow={Math.round(agentPanelWidth)}
						tabIndex={0}
						className="group hidden w-1.5 shrink-0 cursor-col-resize items-center justify-center bg-border/60 outline-none hover:bg-primary/25 focus-visible:bg-primary/30 lg:flex"
						onPointerDown={resizeAgentPanel}
						onKeyDown={(event) => {
							if (event.key === "ArrowLeft") {
								setAgentPanelWidth((width) =>
									Math.max(32, width - 2),
								);
							}
							if (event.key === "ArrowRight") {
								setAgentPanelWidth((width) =>
									Math.min(58, width + 2),
								);
							}
						}}
					>
						<span className="h-8 w-0.5 rounded-full bg-muted-foreground/30 group-hover:bg-primary/60" />
					</div>

					<section
						className={cn(
							"min-h-0 min-w-0 flex-1 flex-col",
							mobilePane === "agent" ? "hidden lg:flex" : "flex",
						)}
					>
						<Tabs
							value={workbenchTab}
							onValueChange={(value) =>
								selectWorkbenchTab(value as BuilderWorkbenchTab)
							}
							className="flex h-full min-h-0 flex-col"
						>
							<div className="hidden min-h-11 items-center border-b px-2 lg:flex">
								<TabsList className="h-8 bg-transparent p-0">
									<TabsTrigger
										value="preview"
										className="gap-1.5"
									>
										<Eye className="h-3.5 w-3.5" />
										Preview
									</TabsTrigger>
									<TabsTrigger
										value="code"
										className="gap-1.5"
									>
										<Code2 className="h-3.5 w-3.5" />
										Code
									</TabsTrigger>
									<TabsTrigger
										value="changes"
										className="gap-1.5"
									>
										<FileDiff className="h-3.5 w-3.5" />
										Changes
										{revisions.length > 0 ? (
											<span className="rounded-full bg-muted px-1.5 text-[10px]">
												{revisions.length}
											</span>
										) : null}
									</TabsTrigger>
								</TabsList>
							</div>
							<TabsContent
								value="preview"
								className="m-0 min-h-0 flex-1"
							>
								<PreviewPane
									key={`${previewRoute}:${previewNonce}`}
									launchUrl={
										previewLaunchQuery.data?.launch_url ??
										null
									}
									state={previewState}
									errorMessage={
										previewLaunchQuery.error instanceof
										Error
											? previewLaunchQuery.error.message
											: null
									}
									route={previewRoute}
									onRouteChange={(route) => {
										setPreviewRoute(route);
										setPreviewNonce((nonce) => nonce + 1);
									}}
									onReload={() =>
										setPreviewNonce((nonce) => nonce + 1)
									}
									isStale={stale}
									device={previewDevice}
									onDeviceChange={setPreviewDevice}
								/>
							</TabsContent>
							<TabsContent
								value="code"
								className="m-0 min-h-0 flex-1"
							>
								<BuilderCodePanel
									solutionId={id}
									revision={source}
								/>
							</TabsContent>
							<TabsContent
								value="changes"
								className="m-0 min-h-0 flex-1"
							>
								<BuilderChangesPanel
									solutionId={id}
									revisions={revisions}
									isLoading={revisionsQuery.isLoading}
									canUndo={Boolean(selectedSession)}
									undoingRevisionId={
										undoMutation.isPending
											? (undoMutation.variables ?? null)
											: null
									}
									onUndo={(revisionId) =>
										undoMutation.mutate(revisionId)
									}
									onDownload={(revisionId) =>
										downloadMutation.mutate(revisionId)
									}
								/>
							</TabsContent>
						</Tabs>
					</section>
				</div>
			</div>
		</TooltipProvider>
	);
}
