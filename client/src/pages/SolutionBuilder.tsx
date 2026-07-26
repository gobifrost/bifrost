/**
 * Private Solution Builder workspace.
 *
 * Layout follows the builder design spec:
 *   left   - builder chat (sessions, messages, composer, turn status)
 *   right  - app preview (route bar + frame)
 *   bottom - collapsible Files / Revisions drawer
 *
 * The chat surface composes the existing chat components (ChatWindow drives
 * itself from a conversation id, which a builder session supplies), so builder
 * state and APIs stay separate from ordinary conversations.
 */

import { useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	ChevronDown,
	ChevronUp,
	Download,
	ExternalLink,
	Loader2,
	Lock,
	MessageSquarePlus,
	Send,
	Undo2,
} from "lucide-react";
import { ChatWindow } from "@/components/chat";
import { PreviewPane } from "@/components/builder/PreviewPane";
import { RevisionList } from "@/components/builder/RevisionList";
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
	undoToRevision,
	type BuilderTurn,
} from "@/services/builder";

/** Poll while a turn is in flight so build status stays live. */
const ACTIVE_POLL_MS = 3000;

function turnBadgeVariant(
	status: BuilderTurn["status"],
): "default" | "secondary" | "destructive" | "outline" {
	if (status === "failed") return "destructive";
	if (status === "succeeded") return "secondary";
	return "default";
}

function turnLabel(turn: BuilderTurn | null): string {
	if (!turn) return "Idle";
	switch (turn.status) {
		case "queued":
			return "Queued";
		case "running":
			return "Building";
		case "succeeded":
			return "Up to date";
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
	const queryClient = useQueryClient();

	const [drawerOpen, setDrawerOpen] = useState(true);
	const [previewRoute, setPreviewRoute] = useState("/");
	const [previewNonce, setPreviewNonce] = useState(0);
	const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
	const [actionError, setActionError] = useState<string | null>(null);

	const id = solutionId ?? "";

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
			return turn && (turn.status === "queued" || turn.status === "running")
				? ACTIVE_POLL_MS
				: false;
		},
	});

	const revisions = useMemo(
		() => revisionsQuery.data ?? [],
		[revisionsQuery.data],
	);
	const sessions = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data]);
	const turn = latestTurn(turnsQuery.data ?? []);
	const stale = isPreviewStale(revisions);
	const source = currentRevision(revisions);
	const deployed = deployedRevision(revisions);

	const selectedSession =
		sessions.find((session) => session.id === activeSessionId) ?? sessions[0];

	// The app-host origin is a deploy-time property the backend does not expose
	// yet; until it does, the preview pane renders its unavailable state.
	const appOrigin: string | null = null;

	const invalidateAll = useCallback(() => {
		queryClient.invalidateQueries({ queryKey: ["builder", "revisions", id] });
		queryClient.invalidateQueries({ queryKey: ["builder", "turns", id] });
	}, [queryClient, id]);

	const newSessionMutation = useMutation({
		mutationFn: () => createBuilderSession(id),
		onSuccess: (session) => {
			queryClient.invalidateQueries({ queryKey: ["builder", "sessions", id] });
			setActiveSessionId(session.id);
		},
		onError: (error: Error) => setActionError(error.message),
	});

	const undoMutation = useMutation({
		mutationFn: (revisionId: string) => {
			if (!selectedSession) {
				throw new Error("Start a builder session before restoring a revision");
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
			queryClient.invalidateQueries({ queryKey: ["builder", "solution", id] }),
		onError: (error: Error) => setActionError(error.message),
	});

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
					{notFound ? "Solution not found" : "Could not open the builder"}
				</h1>
				<p className="max-w-md text-sm text-muted-foreground">
					{notFound
						? "This Solution does not exist, or it is not yours."
						: (error as Error).message}
				</p>
			</div>
		);
	}

	const solution = solutionQuery.data;
	const promotionRequested = solution?.promotion_status === "requested";

	return (
		<TooltipProvider>
			<div className="flex h-full min-h-0 flex-col">
				{/* Top bar */}
				<header className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
					<div className="min-w-0">
						<div className="flex items-center gap-2">
							<h1 className="truncate text-lg font-semibold">
								{solution?.name}
							</h1>
							{solution?.visibility === "private" && (
								<Badge variant="outline" className="gap-1">
									<Lock className="h-3 w-3" />
									Private
								</Badge>
							)}
						</div>
						<p className="text-xs text-muted-foreground">
							{solution?.slug}
						</p>
					</div>

					<Badge
						variant={turnBadgeVariant(turn?.status ?? "succeeded")}
						data-testid="build-status"
					>
						{turnLabel(turn)}
					</Badge>
					{stale && (
						<span
							className="text-xs text-muted-foreground"
							data-testid="source-ahead-note"
						>
							Source is ahead of the preview
						</span>
					)}

					<div className="ml-auto flex flex-wrap items-center gap-2">
						<Button
							variant="outline"
							size="sm"
							disabled={
								!source ||
								!selectedSession ||
								undoMutation.isPending
							}
							onClick={() => {
								const target = source?.parent_revision_id;
								if (target) undoMutation.mutate(target);
							}}
						>
							<Undo2 className="mr-2 h-4 w-4" />
							Undo
						</Button>

						<Button
							variant="outline"
							size="sm"
							disabled={!source || downloadMutation.isPending}
							onClick={() => source && downloadMutation.mutate(source.id)}
						>
							{downloadMutation.isPending ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							) : (
								<Download className="mr-2 h-4 w-4" />
							)}
							Download source
						</Button>

						<Tooltip>
							<TooltipTrigger asChild>
								<span>
									<Button variant="outline" size="sm" disabled={!appOrigin}>
										<ExternalLink className="mr-2 h-4 w-4" />
										Open app
									</Button>
								</span>
							</TooltipTrigger>
							{!appOrigin && (
								<TooltipContent>
									App origin is not configured
								</TooltipContent>
							)}
						</Tooltip>

						<Button
							size="sm"
							disabled={promotionRequested || promotionMutation.isPending}
							onClick={() => promotionMutation.mutate()}
						>
							<Send className="mr-2 h-4 w-4" />
							{promotionRequested
								? "Promotion requested"
								: "Request promotion"}
						</Button>
					</div>
				</header>

				{actionError && (
					<p
						className="border-b bg-destructive/10 px-4 py-2 text-sm text-destructive"
						role="alert"
					>
						{actionError}
					</p>
				)}

				{/* Chat + preview */}
				<div className="flex min-h-0 flex-1 flex-col lg:flex-row">
					<section className="flex min-h-0 flex-1 flex-col border-b lg:w-[45%] lg:flex-none lg:border-b-0 lg:border-r">
						<div className="flex items-center justify-between gap-2 border-b px-3 py-2">
							<span className="text-sm font-medium">Builder chat</span>
							<Button
								variant="ghost"
								size="sm"
								disabled={newSessionMutation.isPending}
								onClick={() => newSessionMutation.mutate()}
							>
								<MessageSquarePlus className="mr-2 h-4 w-4" />
								New session
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
										onClick={() => setActiveSessionId(session.id)}
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
									conversationId={selectedSession.conversation_id}
								/>
							) : (
								<div
									className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center"
									data-testid="no-session"
								>
									<p className="text-sm text-muted-foreground">
										Start a session to describe what you want to build.
									</p>
									<Button
										size="sm"
										disabled={newSessionMutation.isPending}
										onClick={() => newSessionMutation.mutate()}
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

					<section className="flex min-h-0 flex-1 flex-col">
						<PreviewPane
							key={previewNonce}
							appOrigin={appOrigin}
							route={previewRoute}
							onRouteChange={setPreviewRoute}
							onReload={() => setPreviewNonce((nonce) => nonce + 1)}
							isStale={stale}
						/>
					</section>
				</div>

				{/* Files / revisions drawer */}
				<section className="border-t">
					<button
						type="button"
						className="flex w-full items-center justify-between px-4 py-2 text-sm font-medium hover:bg-muted/50"
						aria-expanded={drawerOpen}
						onClick={() => setDrawerOpen((open) => !open)}
					>
						<span>Files and revisions</span>
						{drawerOpen ? (
							<ChevronDown className="h-4 w-4" />
						) : (
							<ChevronUp className="h-4 w-4" />
						)}
					</button>

					{drawerOpen && (
						<Tabs defaultValue="revisions">
							<TabsList className="mx-4">
								<TabsTrigger value="revisions">Revisions</TabsTrigger>
								<TabsTrigger value="files">Files</TabsTrigger>
							</TabsList>

							<TabsContent
								value="revisions"
								className="max-h-64 overflow-auto"
							>
								<RevisionList
									revisions={revisions}
									isLoading={revisionsQuery.isLoading}
									canUndo={Boolean(selectedSession)}
									undoingRevisionId={
										undoMutation.isPending
											? (undoMutation.variables ?? null)
											: null
									}
									onUndo={(revisionId) => undoMutation.mutate(revisionId)}
									onDownload={(revisionId) =>
										downloadMutation.mutate(revisionId)
									}
								/>
							</TabsContent>

							<TabsContent
								value="files"
								className="max-h-64 overflow-auto px-4 py-3"
							>
								{deployed ? (
									<p className="text-sm text-muted-foreground">
										Deployed revision {deployed.id.slice(0, 8)} ·{" "}
										{new Date(deployed.created_at).toLocaleString()}
									</p>
								) : (
									<p className="text-sm text-muted-foreground">
										Nothing deployed yet. Download the source revision to
										inspect its files.
									</p>
								)}
							</TabsContent>
						</Tabs>
					)}
				</section>
			</div>
		</TooltipProvider>
	);
}
