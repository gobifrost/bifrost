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
	AlertTriangle,
	Building2,
	Code2,
	CheckCircle2,
	Database,
	Download,
	Eye,
	ExternalLink,
	FileDiff,
	Loader2,
	Lock,
	MessageSquarePlus,
	RefreshCw,
	RotateCcw,
	Send,
	Square,
	Sparkles,
	Undo2,
	Users,
} from "lucide-react";
import { ChatWindow } from "@/components/chat";
import { BuilderChangesPanel } from "@/components/builder/BuilderChangesPanel";
import { BuilderCodePanel } from "@/components/builder/BuilderCodePanel";
import { BuilderExternalHarnessDialog } from "@/components/builder/BuilderExternalHarnessDialog";
import { BuilderShareDialog } from "@/components/builder/BuilderShareDialog";
import {
	PreviewPane,
	type PreviewDevice,
} from "@/components/builder/PreviewPane";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Progress } from "@/components/ui/progress";
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
	applyGlobalWorkspace,
	createBuilderAppLaunch,
	createBuilderSession,
	currentRevision,
	deployedRevision,
	discardGlobalOperationChange,
	downloadRevision,
	getBuilderSolution,
	getGlobalWorkspace,
	isPreviewStale,
	latestTurn,
	listGlobalOperationChanges,
	listBuilderSessions,
	listRevisions,
	listTurns,
	requestPromotion,
	refreshGlobalWorkspace,
	rollbackGlobalWorkspace,
	runBuilderTurn,
	undoToRevision,
	validateGlobalWorkspace,
	type BuilderBoundary,
	type BuilderTurnStatus,
} from "@/services/builder";
import { useApplications } from "@/hooks/useApplications";
import { useAuth } from "@/contexts/AuthContext";
import {
	loadBuilderWorkbenchState,
	saveBuilderWorkbenchState,
	type BuilderMobilePane,
	type BuilderWorkbenchTab,
} from "@/lib/builder-workbench-state";
import {
	cancelPlatformJob,
	getPlatformJob,
	type PlatformJob,
} from "@/services/platformJobs";
import {
	webSocketService,
	type PlatformJobUpdate,
} from "@/services/websocket";
import { cn } from "@/lib/utils";

function turnBadgeVariant(
	status: BuilderTurnStatus,
): "default" | "secondary" | "destructive" | "outline" {
	if (status === "failed") return "destructive";
	if (status === "succeeded") return "secondary";
	if (status === "cancelled") return "outline";
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
		case "cancelled":
			return "Cancelled";
	}
}

function terminalJob(status: PlatformJob["status"]): boolean {
	return status === "succeeded" || status === "failed" || status === "cancelled";
}

function jobTurnStatus(job: PlatformJob | null): BuilderTurnStatus | null {
	if (!job) return null;
	if (job.status === "succeeded") return "succeeded";
	if (job.status === "failed") return "failed";
	if (job.status === "cancelled") return "cancelled";
	return job.status === "queued" ? "queued" : "running";
}

function buildUsage(job: PlatformJob | null): {
	calls: number;
	tokens: number;
	maxCalls: number | null;
	maxTokens: number | null;
} | null {
	const usage = job?.result?.llm_usage;
	if (!usage || typeof usage !== "object") return null;
	const record = usage as Record<string, unknown>;
	const calls = Number(record.calls ?? 0);
	const input = Number(record.input_tokens ?? 0);
	const output = Number(record.output_tokens ?? 0);
	if (![calls, input, output].every(Number.isFinite)) return null;
	const limits = job?.result?.llm_limits;
	const limitRecord =
		limits && typeof limits === "object"
			? (limits as Record<string, unknown>)
			: null;
	const maxCalls = Number(limitRecord?.max_calls);
	const maxTokens = Number(limitRecord?.max_tokens);
	return {
		calls: Math.max(0, calls),
		tokens: Math.max(0, input) + Math.max(0, output),
		maxCalls: Number.isFinite(maxCalls) && maxCalls > 0 ? maxCalls : null,
		maxTokens: Number.isFinite(maxTokens) && maxTokens > 0 ? maxTokens : null,
	};
}

function compactNumber(value: number): string {
	return new Intl.NumberFormat(undefined, {
		notation: "compact",
		maximumFractionDigits: 1,
	}).format(value);
}

function usagePercent(value: number, limit: number | null): number | null {
	if (!limit) return null;
	return Math.min(100, Math.max(0, Math.round((value / limit) * 100)));
}

function BuilderUsageMeter({
	label,
	value,
	limit,
}: {
	label: string;
	value: number;
	limit: number;
}) {
	const percent = usagePercent(value, limit) ?? 0;
	return (
		<span
			className="flex items-center gap-1.5"
			aria-label={`${label}: ${percent}% of this turn's limit used`}
		>
			<span className="h-1.5 w-12 overflow-hidden rounded-full bg-muted-foreground/20">
				<span
					className="block h-full rounded-full bg-primary transition-[width] motion-reduce:transition-none"
					style={{ width: `${percent}%` }}
				/>
			</span>
			<span className="tabular-nums">{percent}% {label.toLowerCase()}</span>
		</span>
	);
}

function triggerDownload(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = filename;
	anchor.click();
	URL.revokeObjectURL(url);
}

function boundaryFromSearch(search: string): BuilderBoundary | undefined {
	const value = new URLSearchParams(search).get("boundary");
	if (value === "platform" || value === "managed_organizations") return value;
	if (value?.startsWith("organization:")) {
		return value as `organization:${string}`;
	}
	return undefined;
}

export function SolutionBuilder() {
	const { solutionId } = useParams<{ solutionId: string }>();
	const location = useLocation();
	const queryClient = useQueryClient();
	const { user } = useAuth();
	const initialPromptHandled = useRef(false);
	const id = solutionId ?? "";
	const boundary = useMemo(
		() => boundaryFromSearch(location.search),
		[location.search],
	);
	const boundaryOptions = useMemo(
		() => (boundary ? { boundary } : undefined),
		[boundary],
	);
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
	const [shareOpen, setShareOpen] = useState(false);
	const [submittedJobId, setSubmittedJobId] = useState<string | null>(null);
	const [liveJob, setLiveJob] = useState<PlatformJobUpdate | null>(null);
	const [globalConfirmation, setGlobalConfirmation] = useState<
		"apply" | "rollback" | null
	>(null);

	const solutionQuery = useQuery({
		queryKey: ["builder", "solution", id, boundary ?? null],
		queryFn: ({ signal }) => getBuilderSolution(id, { signal, boundary }),
		enabled: Boolean(id),
		retry: false,
	});

	const sessionsQuery = useQuery({
		queryKey: ["builder", "sessions", id, boundary ?? null],
		queryFn: ({ signal }) => listBuilderSessions(id, { signal, boundary }),
		enabled: Boolean(id),
	});

	const revisionsQuery = useQuery({
		queryKey: ["builder", "revisions", id, boundary ?? null],
		queryFn: ({ signal }) => listRevisions(id, { signal, boundary }),
		enabled: Boolean(
			id &&
				solutionQuery.data &&
				solutionQuery.data.target_kind !== "organization",
		),
	});

	const turnsQuery = useQuery({
		queryKey: ["builder", "turns", id, boundary ?? null],
		queryFn: ({ signal }) => listTurns(id, { signal, boundary }),
		enabled: Boolean(id),
	});
	const applicationScope =
		boundary === "platform"
			? "global"
			: boundary?.startsWith("organization:")
				? boundary.slice("organization:".length)
				: undefined;
	const applicationsQuery = useApplications(applicationScope);
	const isGlobalWorkspace =
		solutionQuery.data?.target_kind === "global_repo";
	const isOrganizationWorkspace =
		solutionQuery.data?.target_kind === "organization";
	const globalWorkspaceQuery = useQuery({
		queryKey: ["builder", "global-workspace"],
		queryFn: ({ signal }) => getGlobalWorkspace({ signal }),
		enabled: isGlobalWorkspace,
		retry: false,
	});
	const globalOperationsQuery = useQuery({
		queryKey: ["builder", "global-workspace", "operations"],
		queryFn: ({ signal }) => listGlobalOperationChanges({ signal }),
		enabled: isGlobalWorkspace,
		retry: false,
	});
	const displayedWorkbenchTab =
		(isGlobalWorkspace || isOrganizationWorkspace) && workbenchTab === "preview"
			? "changes"
			: workbenchTab;
	const displayedMobilePane = isOrganizationWorkspace
		? "agent"
		: isGlobalWorkspace && mobilePane === "preview"
			? "changes"
			: mobilePane;

	const revisions = useMemo(
		() => revisionsQuery.data ?? [],
		[revisionsQuery.data],
	);
	const sessions = useMemo(
		() => sessionsQuery.data ?? [],
		[sessionsQuery.data],
	);
	const turn = latestTurn(turnsQuery.data ?? []);
	const activeJobId =
		submittedJobId ??
		(turn?.status === "queued" || turn?.status === "running" ? turn.id : null);
	const platformJobQuery = useQuery({
		queryKey: ["platform-job", activeJobId],
		queryFn: ({ signal }) => getPlatformJob(activeJobId!, signal),
		enabled: Boolean(activeJobId),
		retry: false,
	});
	const platformJob =
		liveJob?.id === activeJobId
			? liveJob
			: (platformJobQuery.data ?? null);
	const buildInProgress = Boolean(
		activeJobId && (!platformJob || !terminalJob(platformJob.status)),
	);
	const stale = isPreviewStale(revisions);
	const source = currentRevision(revisions);
	const deployed = deployedRevision(revisions);
	const solution = solutionQuery.data;
	const canEdit = Boolean(
		solution &&
			(solution.caller_access === "owner" ||
				solution.caller_access === "support" ||
				solution.collaborator_access === "edit"),
	);
	const canManage = Boolean(
		solution &&
			(solution.caller_access === "owner" || solution.caller_access === "support"),
	);
	const workbenchStateError = !isOrganizationWorkspace && revisionsQuery.isError
		? revisionsQuery.error
		: turnsQuery.isError
			? turnsQuery.error
			: null;
	const workbenchStateUnavailable = Boolean(
		sessionsQuery.isError ||
			(!isOrganizationWorkspace && revisionsQuery.isError) ||
			turnsQuery.isError,
	);
	const builderApp = applicationsQuery.data?.applications.find(
		(app) => app.solution_id === id,
	);
	const selectedSession =
		sessions.find((session) => session.id === activeSessionId) ??
		sessions.find((session) => session.id === initialSessionId) ??
		sessions[0];
	const selectedSessionTurn = (turnsQuery.data ?? []).find(
		(candidate) => candidate.session_id === selectedSession?.id,
	);
	const resumableTurn =
		selectedSessionTurn?.checkpoint_available &&
		(selectedSessionTurn.status === "failed" ||
			selectedSessionTurn.status === "cancelled")
			? selectedSessionTurn
			: null;

	const canLaunchApp = Boolean(
		!isGlobalWorkspace && !isOrganizationWorkspace && builderApp && deployed,
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
				boundary,
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
		queryClient.invalidateQueries({
			queryKey: ["builder", "global-workspace"],
		});
		if (selectedSession) {
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/chat/conversations/{conversation_id}/messages",
				],
			});
		}
	}, [queryClient, id, selectedSession]);

	useEffect(() => {
		if (!activeJobId) {
			return;
		}
		if (user?.id) {
			void webSocketService.connect([`notification:${user.id}`]);
		}
		return webSocketService.onPlatformJobUpdate(activeJobId, (job) => {
			setLiveJob(job);
			queryClient.setQueryData(["platform-job", activeJobId], job);
			if (!terminalJob(job.status)) return;
			invalidateAll();
			queryClient.invalidateQueries({
				queryKey: ["builder", "global-workspace", "operations"],
			});
		});
	}, [activeJobId, invalidateAll, queryClient, user?.id]);

	const newSessionMutation = useMutation({
		mutationFn: () =>
			boundaryOptions
				? createBuilderSession(id, boundaryOptions)
				: createBuilderSession(id),
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
			const request = {
				toRevisionId: revisionId,
				sessionId: selectedSession.id,
			};
			return boundaryOptions
				? undoToRevision(id, request, boundaryOptions)
				: undoToRevision(id, request);
		},
		onSuccess: invalidateAll,
		onError: (error: Error) => setActionError(error.message),
	});

	const downloadMutation = useMutation({
		mutationFn: (revisionId: string) =>
			boundaryOptions
				? downloadRevision(id, revisionId, boundaryOptions)
				: downloadRevision(id, revisionId),
		onSuccess: ({ blob, filename }) => triggerDownload(blob, filename),
		onError: (error: Error) => setActionError(error.message),
	});

	const promotionMutation = useMutation({
		mutationFn: () =>
			boundaryOptions
				? requestPromotion(id, boundaryOptions)
				: requestPromotion(id),
		onSuccess: () =>
			queryClient.invalidateQueries({
				queryKey: ["builder", "solution", id],
			}),
		onError: (error: Error) => setActionError(error.message),
	});

	const globalValidationMutation = useMutation({
		mutationFn: () => validateGlobalWorkspace(),
		onMutate: () => setActionError(null),
		onError: (error: Error) => setActionError(error.message),
	});

	const globalRefreshMutation = useMutation({
		mutationFn: () => refreshGlobalWorkspace(),
		onMutate: () => setActionError(null),
		onSuccess: () => {
			globalValidationMutation.reset();
			invalidateAll();
		},
		onError: (error: Error) => setActionError(error.message),
	});

	const discardGlobalOperationMutation = useMutation({
		mutationFn: (changeId: string) => discardGlobalOperationChange(changeId),
		onMutate: () => setActionError(null),
		onSuccess: () => {
			globalValidationMutation.reset();
			invalidateAll();
			queryClient.invalidateQueries({
				queryKey: ["builder", "global-workspace", "operations"],
			});
		},
		onError: (error: Error) => setActionError(error.message),
	});

	const globalApplyMutation = useMutation({
		mutationFn: () => applyGlobalWorkspace(),
		onMutate: () => setActionError(null),
		onSuccess: (result) => {
			setSubmittedJobId(result.job_id);
			setGlobalConfirmation(null);
			globalValidationMutation.reset();
			invalidateAll();
			queryClient.invalidateQueries({
				queryKey: ["builder", "global-workspace", "operations"],
			});
		},
		onError: (error: Error) => {
			setGlobalConfirmation(null);
			setActionError(error.message);
		},
	});

	const globalRollbackMutation = useMutation({
		mutationFn: () => rollbackGlobalWorkspace(),
		onMutate: () => setActionError(null),
		onSuccess: (result) => {
			setSubmittedJobId(result.job_id);
			setGlobalConfirmation(null);
			globalValidationMutation.reset();
			invalidateAll();
			queryClient.invalidateQueries({
				queryKey: ["builder", "global-workspace", "operations"],
			});
		},
		onError: (error: Error) => {
			setGlobalConfirmation(null);
			setActionError(error.message);
		},
	});

	const cancelMutation = useMutation({
		mutationFn: (jobId: string) => cancelPlatformJob(jobId),
		onMutate: () => setActionError(null),
		onSuccess: () => {
			if (activeJobId) {
				queryClient.invalidateQueries({
					queryKey: ["platform-job", activeJobId],
				});
			}
		},
		onError: (error: Error) => setActionError(error.message),
	});

	const runTurnMutation = useMutation({
		mutationFn: (params: {
			sessionId: string;
			message: string;
			attachmentIds?: string[];
			resumeFromTurnId?: string;
		}) =>
			boundaryOptions
				? runBuilderTurn(id, params, boundaryOptions)
				: runBuilderTurn(id, params),
		onMutate: () => setActionError(null),
		onSuccess: (result) => {
			setSubmittedJobId(result.job_id);
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

	const handleBuilderMessage = useCallback(
		(message: string, attachmentIds: string[]) => {
			if (!canEdit) {
				setActionError("You have view-only access to this build");
				return;
			}
			if (workbenchStateUnavailable) {
				setActionError(
					"Restore this build's sessions and history before making another change",
				);
				return;
			}
			if (!selectedSession) {
				setActionError(
					"Start a builder session before sending a message",
				);
				return;
			}
			if (runTurnMutation.isPending || buildInProgress) {
				setActionError(
					"This build is still running. Wait for it to finish or cancel it before sending another change.",
				);
				return;
			}
			runTurnMutation.mutate({
				sessionId: selectedSession.id,
				message,
				attachmentIds,
			});
		},
		[
			buildInProgress,
			canEdit,
			runTurnMutation,
			selectedSession,
			workbenchStateUnavailable,
		],
	);

	const handleResumeCheckpoint = useCallback(() => {
		if (
			!canEdit ||
			!selectedSession ||
			!resumableTurn ||
			buildInProgress ||
			workbenchStateUnavailable
		) {
			return;
		}
		runTurnMutation.mutate({
			sessionId: selectedSession.id,
			message:
				"Continue from the saved checkpoint and finish the original request.",
			resumeFromTurnId: resumableTurn.id,
		});
	}, [
		buildInProgress,
		canEdit,
		resumableTurn,
		runTurnMutation,
		selectedSession,
		workbenchStateUnavailable,
	]);

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

	const displayedTurnStatus: BuilderTurnStatus | null = runTurnMutation.isPending
		? "running"
		: (jobTurnStatus(platformJob) ?? turn?.status ?? null);
	const buildActive = Boolean(
		runTurnMutation.isPending || buildInProgress,
	);
	const usage = buildUsage(platformJob);
	const durableError =
		platformJob?.status === "failed" ? platformJob.error?.message : null;
	const globalOperationChanges = globalOperationsQuery.data?.changes ?? [];
	const globalOperationRollbackChanges =
		globalOperationsQuery.data?.rollbackable_changes ?? [];
	const globalWorkspaceWithOperations = globalWorkspaceQuery.data as
		| (typeof globalWorkspaceQuery.data & { pending_operation_count?: number })
		| undefined;
	const hasGlobalRollback =
		Boolean(globalWorkspaceQuery.data?.can_rollback) ||
		globalOperationRollbackChanges.length > 0;
	const pendingGlobalOperationCount =
		globalWorkspaceWithOperations?.pending_operation_count ??
		globalOperationChanges.length;
	const hasGlobalOperationProposal = pendingGlobalOperationCount > 0;
	const hasGlobalProposal = Boolean(
		isGlobalWorkspace &&
			((source && deployed && source.id !== deployed.id) ||
				hasGlobalOperationProposal),
	);
	const globalValidationIsCurrent = Boolean(
		globalValidationMutation.data?.revision_id === source?.id,
	);
	const globalProposalIsValid = Boolean(
		globalValidationIsCurrent && globalValidationMutation.data?.valid,
	);
	const globalActionPending =
		globalValidationMutation.isPending ||
		globalRefreshMutation.isPending ||
		discardGlobalOperationMutation.isPending ||
		globalApplyMutation.isPending ||
		globalRollbackMutation.isPending;
	const promotionReady = Boolean(
		!isGlobalWorkspace &&
		!isOrganizationWorkspace &&
		canManage &&
		!workbenchStateUnavailable &&
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
		"waiting" | "loading" | "failed" | "ready" =
		!builderApp || !deployed
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
				{solution && !isGlobalWorkspace ? (
					<BuilderShareDialog
						solutionId={solution.id}
						solutionName={solution.name}
						boundary={boundary}
						open={shareOpen}
						onOpenChange={setShareOpen}
					/>
				) : null}
				<AlertDialog
				open={globalConfirmation === "apply"}
				onOpenChange={(open) => !open && setGlobalConfirmation(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Apply this release to the live platform?</AlertDialogTitle>
						<AlertDialogDescription>
							Bifrost will apply the exact source diff and operation changes you reviewed. Source changes run before resource changes; if anything changed after review, the release stops.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Keep reviewing</AlertDialogCancel>
						<AlertDialogAction
							disabled={globalApplyMutation.isPending}
							onClick={() => globalApplyMutation.mutate()}
						>
							{globalApplyMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
							Apply release
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
			<AlertDialog
				open={globalConfirmation === "rollback"}
				onOpenChange={(open) => !open && setGlobalConfirmation(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Roll back the latest Global release?</AlertDialogTitle>
						<AlertDialogDescription>
							Bifrost will reverse the latest reviewed release batch. Resource operations roll back before source is restored, and newer edits are never overwritten.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							variant="destructive"
							disabled={globalRollbackMutation.isPending}
							onClick={() => globalRollbackMutation.mutate()}
						>
							{globalRollbackMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
							Roll back release
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
			<header className="flex flex-wrap items-center gap-3 border-b px-3 py-2.5 sm:px-4">
					<div className="min-w-0 flex-1">
						<div className="flex min-w-0 items-center gap-2">
							<h1 className="truncate text-base font-semibold sm:text-lg">
								{solution?.name}
							</h1>
						{isGlobalWorkspace ? (
							<Badge variant="outline" className="shrink-0 gap-1 border-primary/30 text-primary">
								<Database className="h-3 w-3" />
								Live _repo
							</Badge>
						) : isOrganizationWorkspace ? (
							<Badge variant="outline" className="shrink-0 gap-1 border-primary/30 text-primary">
								<Building2 className="h-3 w-3" />
								Organization workspace
							</Badge>
						) : solution?.visibility === "private" ? (
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
						{isGlobalWorkspace
							? "Instance-wide source · changes require explicit apply"
							: isOrganizationWorkspace
								? `${solution?.organization_name ?? "Organization"} · authorized changes apply directly`
							: `${solution?.organization_name ?? "Build"} / ${solution?.slug}${
									solution?.caller_access !== "owner" && solution?.owner_name
										? ` · Owned by ${solution.owner_name}`
										: ""
								}`}
					</p>
					</div>

					<div className="flex w-full items-center gap-1.5 overflow-x-auto sm:w-auto">
						<Badge variant="secondary" className="h-7 shrink-0 gap-1.5">
						{isGlobalWorkspace ? (
							<Database className="h-3 w-3" />
						) : solution?.caller_access === "owner" ? (
								<Lock className="h-3 w-3" />
							) : (
								<Users className="h-3 w-3" />
							)}
						{isGlobalWorkspace
							? "Admin only"
							: solution?.caller_access === "owner"
								? "Owner"
								: solution?.caller_access === "support"
									? "Support"
									: solution?.collaborator_access === "edit"
										? "Can edit"
										: "View only"}
						</Badge>
					{canManage && !isGlobalWorkspace ? (
							<Button
								variant="ghost"
								size="sm"
								className="h-9 sm:h-7"
								onClick={() => setShareOpen(true)}
							>
								<Users className="h-4 w-4" />
								<span className="hidden xl:inline">Share</span>
							</Button>
						) : null}
						{!isOrganizationWorkspace ? <Button
							variant="ghost"
							size="sm"
							className="h-9 sm:h-7"
						aria-label={isGlobalWorkspace ? "Undo latest proposal change" : "Undo latest source change"}
								disabled={
									!canEdit ||
									workbenchStateUnavailable ||
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
						</Button> : null}
						{!isOrganizationWorkspace ? <Button
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
						</Button> : null}
					{!isGlobalWorkspace && !isOrganizationWorkspace ? <Tooltip>
							<TooltipTrigger asChild>
								<span>
									<Button
										variant="outline"
										size="sm"
										className="h-9 sm:h-7"
										aria-label="Open app"
									disabled={
										!canLaunchApp
									}
									onClick={() => {
										if (!builderApp) return;
										const route = previewRoute === "/" ? "" : previewRoute;
										window.open(
											`/apps/${builderApp.slug}${route}`,
											"_blank",
											"noopener,noreferrer",
										);
									}}
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
									The app needs a successful deploy first
								</TooltipContent>
							) : null}
					</Tooltip> : null}
					{!isGlobalWorkspace && !isOrganizationWorkspace ? <Tooltip>
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
										!canManage ||
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
					</Tooltip> : null}
				</div>
			</header>

			{workbenchStateError ? (
				<div
					className="flex flex-col gap-3 border-b bg-destructive/10 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
					role="alert"
				>
					<div className="flex min-w-0 items-start gap-2">
						<AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
						<div>
							<p className="font-medium text-destructive">Could not restore build history</p>
							<p className="text-xs text-muted-foreground">{workbenchStateError.message}</p>
						</div>
					</div>
					<Button
						variant="outline"
						size="sm"
						className="shrink-0 bg-background"
						onClick={() => {
							if (revisionsQuery.isError) void revisionsQuery.refetch();
							if (turnsQuery.isError) void turnsQuery.refetch();
						}}
					>
						Try again
					</Button>
				</div>
			) : null}

			{isGlobalWorkspace ? (
				<div className="border-b border-primary/15 bg-primary/[0.045] px-3 py-3 sm:px-4">
					<div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
						<div className="min-w-0">
							<div className="flex flex-wrap items-center gap-2">
								<p className="text-sm font-medium">
									{hasGlobalProposal
										? globalProposalIsValid
											? "Validated proposal ready to apply"
											: "Review and validate this proposal"
										: "Proposal matches live _repo"}
								</p>
								{globalProposalIsValid ? (
									<Badge variant="secondary" className="gap-1 text-emerald-700 dark:text-emerald-300">
										<CheckCircle2 className="h-3 w-3" /> Validated
									</Badge>
								) : null}
							</div>
						<p className="mt-1 text-xs leading-5 text-muted-foreground">
							AI edits only the immutable proposal. Refresh and rollback both stop if they would overwrite newer live work.
						</p>
						{globalWorkspaceQuery.isError ? (
							<p className="mt-2 text-xs text-destructive" role="alert">
								{(globalWorkspaceQuery.error as Error).message}
							</p>
						) : null}
							{globalValidationIsCurrent && !globalValidationMutation.data?.valid ? (
								<ul className="mt-2 space-y-1 text-xs text-destructive" role="alert">
								{(globalValidationMutation.data?.errors ?? []).slice(0, 3).map((validationError) => (
										<li key={validationError}>{validationError}</li>
									))}
								</ul>
							) : null}
							{globalOperationChanges.length > 0 ? (
								<div className="mt-3 space-y-2 rounded-lg border bg-background/70 p-3">
									<div className="flex items-center justify-between gap-2">
										<p className="text-xs font-medium">
											{globalOperationChanges.length} staged operation change{globalOperationChanges.length === 1 ? "" : "s"}
										</p>
										<Badge variant="outline">Human review required</Badge>
									</div>
									<div className="space-y-2">
										{globalOperationChanges.map((change) => (
											<div key={change.id} className="rounded-md border bg-muted/30 p-2 text-xs">
												<div className="flex flex-wrap items-center justify-between gap-2">
													<div>
														<p className="font-medium">{change.operation_id}</p>
														<p className="text-muted-foreground">
															{change.resource_type}{change.resource_id ? ` · ${change.resource_id}` : " · new global resource"}
														</p>
													</div>
													<Button
														variant="ghost"
														size="sm"
														className="h-7"
														disabled={globalActionPending}
														onClick={() => discardGlobalOperationMutation.mutate(change.id)}
													>
														Discard
													</Button>
												</div>
												{change.validation_errors.length > 0 ? (
													<ul className="mt-2 space-y-1 text-destructive">
														{change.validation_errors.map((error) => (
															<li key={error}>{error}</li>
														))}
													</ul>
												) : null}
												<div className="mt-2 grid gap-2 md:grid-cols-2">
													<div>
														<p className="mb-1 text-[11px] font-medium text-muted-foreground">
															Before
														</p>
														{change.before_state ? (
															<pre className="max-h-32 overflow-auto rounded bg-background p-2 text-[11px]">
																{JSON.stringify(change.before_state, null, 2)}
															</pre>
														) : (
															<p className="rounded bg-background p-2 text-[11px] text-muted-foreground">
																New global resource
															</p>
														)}
													</div>
													<div>
														<p className="mb-1 text-[11px] font-medium text-muted-foreground">
															Proposed
														</p>
														<pre className="max-h-32 overflow-auto rounded bg-background p-2 text-[11px]">
															{JSON.stringify(change.after_state, null, 2)}
														</pre>
													</div>
												</div>
											</div>
										))}
									</div>
								</div>
							) : null}
							{globalOperationRollbackChanges.length > 0 ? (
								<div className="mt-3 space-y-2 rounded-lg border bg-background/70 p-3">
									<div className="flex items-center justify-between gap-2">
										<p className="text-xs font-medium">
											Latest Global release
										</p>
										<Badge variant="outline">Rollback review required</Badge>
									</div>
									<div className="space-y-2">
										{globalOperationRollbackChanges.map((change) => (
											<div key={change.id} className="rounded-md border bg-muted/30 p-2 text-xs">
												<p className="font-medium">{change.operation_id}</p>
												<p className="text-muted-foreground">
													{change.resource_type}{change.resource_id ? ` · ${change.resource_id}` : " · global resource"}
												</p>
												<div className="mt-2 grid gap-2 md:grid-cols-2">
													<div>
														<p className="mb-1 text-[11px] font-medium text-muted-foreground">
															Before
														</p>
														{change.before_state ? (
															<pre className="max-h-32 overflow-auto rounded bg-background p-2 text-[11px]">
																{JSON.stringify(change.before_state, null, 2)}
															</pre>
														) : (
															<p className="rounded bg-background p-2 text-[11px] text-muted-foreground">
																Resource did not exist
															</p>
														)}
													</div>
													<div>
														<p className="mb-1 text-[11px] font-medium text-muted-foreground">
															Applied
														</p>
														<pre className="max-h-32 overflow-auto rounded bg-background p-2 text-[11px]">
															{JSON.stringify(change.after_state, null, 2)}
														</pre>
													</div>
												</div>
												{change.before_state ? null : (
													<p className="mt-2 text-[11px] text-destructive">
														This will delete the created {change.resource_type}.
													</p>
												)}
											</div>
										))}
									</div>
								</div>
							) : null}
						</div>
						<div className="flex flex-wrap items-center gap-2">
							<Button
								variant="ghost"
								size="sm"
								disabled={hasGlobalProposal || buildActive || globalActionPending}
								onClick={() => globalRefreshMutation.mutate()}
							>
								{globalRefreshMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
								Refresh from live
							</Button>
							<Button
								variant="outline"
								size="sm"
								disabled={!hasGlobalProposal || buildActive || globalActionPending}
								onClick={() => globalValidationMutation.mutate()}
							>
								{globalValidationMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
								Validate proposal
							</Button>
							<Button
								size="sm"
								disabled={
									!globalProposalIsValid ||
									buildActive ||
									globalActionPending
								}
								onClick={() => setGlobalConfirmation("apply")}
							>
							{globalApplyMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
								Apply release
							</Button>
							<Button
								variant="ghost"
								size="sm"
								className="text-destructive hover:text-destructive"
								disabled={!hasGlobalRollback || hasGlobalProposal || buildActive || globalActionPending}
								onClick={() => setGlobalConfirmation("rollback")}
							>
							{globalRollbackMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
								Roll back
							</Button>
						</div>
					</div>
				</div>
			) : null}

			<div className="border-b bg-muted/25" aria-live="polite">
					<div className="flex min-h-10 items-center gap-2 px-3 py-1.5 text-xs sm:px-4">
						<div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
							<Badge
								variant={turnBadgeVariant(
									displayedTurnStatus ?? "succeeded",
								)}
								className="shrink-0 gap-1.5"
								data-testid="build-status"
							>
								{buildActive ? (
									<Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />
								) : null}
							{platformJob?.status === "cancel_requested"
								? "Cancelling"
								: isGlobalWorkspace && displayedTurnStatus === "running"
									? "Preparing proposal"
									: turnLabel(displayedTurnStatus)}
						</Badge>
						<span className="flex shrink-0 items-center gap-1.5 text-muted-foreground">
							{isGlobalWorkspace ? <Database className="h-3.5 w-3.5 text-primary" /> : isOrganizationWorkspace ? <Building2 className="h-3.5 w-3.5 text-primary" /> : <Sparkles className="h-3.5 w-3.5 text-primary" />}
							{isGlobalWorkspace ? (
								<strong className="font-medium text-foreground">Global Workspace agent</strong>
							) : isOrganizationWorkspace ? (
								<strong className="font-medium text-foreground">Organization Builder</strong>
							) : (
								<>
									Skill{" "}
									<strong
										className="font-medium text-foreground"
										data-testid="active-builder-skill"
									>
										bifrost-build
									</strong>
								</>
							)}
							</span>
							{usage ? (
								<span
									className="flex shrink-0 items-center gap-2 text-muted-foreground"
									data-testid="build-usage"
								>
									<span>
										{usage.calls}
										{usage.maxCalls ? ` of ${usage.maxCalls}` : ""} AI calls ·{" "}
										{compactNumber(usage.tokens)}
										{usage.maxTokens
											? ` of ${compactNumber(usage.maxTokens)}`
											: ""}{" "}
										tokens
									</span>
									{usage.maxCalls && usage.maxTokens ? (
										<span
											className="hidden items-center gap-2 rounded-full border bg-background/70 px-2 py-1 lg:flex"
											data-testid="build-usage-percentages"
										>
											<BuilderUsageMeter
												label="Calls"
												value={usage.calls}
												limit={usage.maxCalls}
											/>
							<span aria-hidden="true"> · </span>
											<BuilderUsageMeter
												label="Tokens"
												value={usage.tokens}
												limit={usage.maxTokens}
											/>
										</span>
									) : null}
								</span>
							) : null}
							{platformJob?.external_provider ? (
								<span className="hidden shrink-0 text-muted-foreground md:inline">
									{platformJob.external_provider} runner
								</span>
							) : null}
							{!isOrganizationWorkspace ? <span className="hidden shrink-0 items-center gap-1 text-muted-foreground xl:flex">
							{isGlobalWorkspace ? "Proposal" : "Source"}{" "}
								<code className="text-foreground">
									{source?.id.slice(0, 8) ?? "none"}
								</code>
								<span aria-hidden="true">→</span>
							{isGlobalWorkspace ? "Live" : "Preview"}{" "}
								<code className="text-foreground">
									{deployed?.id.slice(0, 8) ?? "not deployed"}
								</code>
							</span> : null}
							{!isOrganizationWorkspace && stale ? (
								<span
									className="shrink-0 text-amber-600 dark:text-amber-400"
									data-testid="source-ahead-note"
								>
							{isGlobalWorkspace ? "Proposal not applied" : "Preview is behind source"}
								</span>
							) : null}
							<span
							className={cn(
								"ml-auto hidden shrink-0 items-center gap-1.5 2xl:flex",
								isOrganizationWorkspace ? "text-emerald-600 dark:text-emerald-400" : isGlobalWorkspace ? (globalProposalIsValid ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground") : promotionReady
										? "text-emerald-600 dark:text-emerald-400"
										: "text-muted-foreground",
								)}
							>
								<span
									className={cn(
										"h-1.5 w-1.5 rounded-full",
									isOrganizationWorkspace ? "bg-emerald-500" : isGlobalWorkspace ? (globalProposalIsValid ? "bg-emerald-500" : "bg-muted-foreground/50") : promotionReady
											? "bg-emerald-500"
											: "bg-muted-foreground/50",
									)}
								/>
							{isOrganizationWorkspace
								? "Changes use your current organization permissions"
								: isGlobalWorkspace
								? globalProposalIsValid
									? "Validated for explicit apply"
									: hasGlobalProposal
										? "Proposal requires validation"
										: "Live source is current"
								: promotionRequested
									? "Awaiting admin review"
									: promotionReady
										? "Ready for review"
										: promotionGuidance}
							</span>
						</div>
						{activeJobId && platformJob?.can_cancel ? (
							<Button
								variant="ghost"
								size="sm"
								className="h-8 shrink-0 gap-1.5"
								disabled={cancelMutation.isPending}
								onClick={() => cancelMutation.mutate(activeJobId)}
							>
								{cancelMutation.isPending ? (
									<Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
								) : (
									<Square className="h-3 w-3 fill-current" />
								)}
								Cancel
							</Button>
						) : null}
					</div>
					{buildActive && platformJob?.progress.percent != null ? (
						<Progress
							value={platformJob.progress.percent}
							className="h-0.5 rounded-none"
						/>
					) : null}
				</div>

				{actionError && (
					<p
						className="border-b bg-destructive/10 px-4 py-2 text-sm text-destructive"
						role="alert"
					>
						{actionError}
					</p>
				)}
				{!actionError && durableError ? (
					<p
						className="border-b bg-destructive/10 px-4 py-2 text-sm text-destructive"
						role="alert"
					>
						{durableError}
					</p>
				) : null}
				{!actionError && !durableError && turn?.status === "failed" && turn.error && (
					<p
						className="border-b bg-destructive/10 px-4 py-2 text-sm text-destructive"
						role="alert"
					>
						{turn.error}
					</p>
				)}
				{resumableTurn && canEdit && !buildActive && !workbenchStateUnavailable ? (
					<div
						className="flex flex-col gap-2 border-b border-amber-500/20 bg-amber-500/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
						data-testid="builder-checkpoint"
					>
						<div className="min-w-0">
							<p className="text-sm font-medium text-foreground">
								Partial work was saved
							</p>
							<p className="text-xs text-muted-foreground">
								Resume this session from its isolated checkpoint, or send a new request to start from the current app.
							</p>
						</div>
						<Button
							variant="outline"
							size="sm"
							className="shrink-0 bg-background"
							onClick={handleResumeCheckpoint}
						>
							<Undo2 className="h-3.5 w-3.5" />
							Resume saved work
						</Button>
					</div>
				) : null}
				{!canEdit && !actionError ? (
					<p className="border-b bg-muted/35 px-4 py-2 text-xs text-muted-foreground">
						You are reviewing this build. Conversation, source, and preview are available; only editors can make changes.
					</p>
				) : null}

			{!isOrganizationWorkspace ? <div className={cn("grid border-b p-1 lg:hidden", isGlobalWorkspace ? "grid-cols-3" : "grid-cols-4")}>
				{(
						[
							["agent", Sparkles, "Agent"],
							["preview", Eye, "Preview"],
							["code", Code2, "Code"],
							["changes", FileDiff, "Changes"],
					] as const
				).filter(([value]) => !isGlobalWorkspace || value !== "preview").map(([value, Icon, label]) => (
						<Button
							key={value}
							variant={
							displayedMobilePane === value ? "secondary" : "ghost"
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
				</div> : null}

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
							"min-h-0 flex-1 flex-col border-b lg:flex-none lg:border-b-0",
							isOrganizationWorkspace ? "lg:w-full" : "lg:w-[var(--agent-panel-width)] lg:border-r",
					displayedMobilePane === "agent" ? "flex" : "hidden lg:flex",
						)}
					>
						<div className="flex min-h-11 items-center justify-between gap-2 border-b px-3 py-2">
							<div className="min-w-0">
								<div className="flex items-center gap-2 text-sm font-medium">
							{isGlobalWorkspace ? "Workspace Agent" : isOrganizationWorkspace ? "Organization Builder" : "Agent"}
									<Badge
										variant="outline"
										className="gap-1 border-primary/20 text-[10px] text-primary"
									>
										<Sparkles className="h-2.5 w-2.5" />
								{isGlobalWorkspace ? "Admin instructions" : "bifrost-build"}
									</Badge>
								</div>
								<p className="truncate text-[11px] text-muted-foreground">
							{isGlobalWorkspace
								? "Proposes bounded _repo edits; never applies them"
								: isOrganizationWorkspace
									? "Uses only the tools allowed in this organization"
								: "bifrost-build guides each generated change"}
								</p>
							</div>
								<div className="flex items-center gap-1">
									{!isGlobalWorkspace ? (
									<BuilderExternalHarnessDialog
										session={selectedSession}
										targetKind={solution?.target_kind}
									/>
									) : null}
									<Button
										variant="ghost"
										size="sm"
										disabled={!canEdit || workbenchStateUnavailable || newSessionMutation.isPending}
										onClick={() => newSessionMutation.mutate()}
									>
										<MessageSquarePlus className="h-4 w-4" />
										<span className="hidden xl:inline">
											New session
										</span>
									</Button>
								</div>
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
							) : sessionsQuery.isError ? (
								<div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center" role="alert">
									<AlertTriangle className="h-7 w-7 text-destructive" />
									<div>
										<p className="text-sm font-medium">Could not restore sessions</p>
										<p className="mt-1 text-xs text-muted-foreground">{sessionsQuery.error.message}</p>
									</div>
									<Button variant="outline" size="sm" onClick={() => void sessionsQuery.refetch()}>Try again</Button>
								</div>
							) : selectedSession ? (
								<ChatWindow
									conversationId={
										selectedSession.conversation_id
									}
									agentName={isGlobalWorkspace ? "Global Workspace Agent" : isOrganizationWorkspace ? "Organization Builder" : "App Builder"}
									onSend={handleBuilderMessage}
									isSending={buildActive}
									inputDisabled={!canEdit || buildActive || workbenchStateUnavailable}
									inputPlaceholder={
										buildActive
								? "Bifrost is working…"
									: isGlobalWorkspace
										? "Describe a change to propose for the global workspace…"
										: isOrganizationWorkspace
											? "Describe the resources to create or change in this organization…"
									: "Describe what to build or change…"
									}
								/>
							) : (
								<div
									className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center"
									data-testid="no-session"
								>
									<p className="text-sm text-muted-foreground">
							{isGlobalWorkspace
								? "Start a session to describe the instance-wide change you want to propose."
								: isOrganizationWorkspace
									? "Start a session to create or change resources in this organization."
								: "Start a session to describe what you want to build."}
									</p>
									<Button
										size="sm"
									disabled={!canEdit || workbenchStateUnavailable || newSessionMutation.isPending}
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
					className={cn("group hidden w-1.5 shrink-0 cursor-col-resize items-center justify-center bg-border/60 outline-none hover:bg-primary/25 focus-visible:bg-primary/30", !isOrganizationWorkspace && "lg:flex")}
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

					{!isOrganizationWorkspace ? <section
						className={cn(
							"min-h-0 min-w-0 flex-1 flex-col",
					displayedMobilePane === "agent" ? "hidden lg:flex" : "flex",
						)}
					>
				<Tabs
					value={displayedWorkbenchTab}
							onValueChange={(value) =>
								selectWorkbenchTab(value as BuilderWorkbenchTab)
							}
							className="flex h-full min-h-0 flex-col"
						>
							<div className="hidden min-h-11 items-center border-b px-2 lg:flex">
								<TabsList className="h-8 bg-transparent p-0">
							{!isGlobalWorkspace ? <TabsTrigger
								value="preview"
										className="gap-1.5"
									>
										<Eye className="h-3.5 w-3.5" />
										Preview
							</TabsTrigger> : null}
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
					{!isGlobalWorkspace ? <TabsContent
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
									isBuilding={buildActive}
									buildDetail={
										platformJob?.progress.phase ?? undefined
									}
									device={previewDevice}
									onDeviceChange={setPreviewDevice}
								/>
					</TabsContent> : null}
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
									canUndo={canEdit && !workbenchStateUnavailable && Boolean(selectedSession)}
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
					</section> : null}
				</div>
			</div>
		</TooltipProvider>
	);
}
