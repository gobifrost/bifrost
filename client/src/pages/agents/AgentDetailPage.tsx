import { useMediaQuery } from "@/hooks/useMediaQuery";
import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { EntityLogo } from "@/components/EntityLogo";
import { AgentDeleteDialog } from "./AgentDeleteDialog";
import { FleetReadError } from "./FleetReadError";
import { Skeleton } from "@/components/ui/skeleton";
/**
 * AgentDetailPage — single page handling both edit and create modes.
 *
 * Routes:
 *   /agents/:id  → edit mode (all 3 tabs active; Overview + Runs load data)
 *   /agents/new  → create mode (Overview + Runs disabled; Settings only)
 *
 * Visual spec mirrors /tmp/agent-mockup/src/pages/AgentDetailPage.tsx: breadcrumb,
 * header with name + Active/Paused pill + description + action row, pill tabs with
 * run-count badge, plus per-tab body (Overview/Runs/Settings).
 */

import { useRef, useState } from "react";
import {
	Link,
	useNavigate,
	useParams,
	useSearchParams,
} from "react-router-dom";
import { RunActionFeedback } from "./RunActionFeedback";
import {
	ArrowLeft,
	Bot,
	Loader2,
	MessageSquare,
	Pause,
	PlayCircle,
	Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";

import { AgentLogoEditor } from "./AgentLogoEditor";
import { AgentOverviewTab } from "@/components/agents/AgentOverviewTab";
import { AgentRunsTab } from "@/components/agents/AgentRunsTab";
import { AgentSettingsTab } from "@/components/agents/AgentSettingsTab";
import { PillTabs } from "@/components/agents/PillTabs";
import { SummaryBackfillButton } from "@/components/agents/SummaryBackfillButton";
import {
	PILL_ACTIVE,
	TONE_MUTED,
	TYPE_BODY,
	TYPE_PAGE_TITLE,
} from "@/components/agents/design-tokens";
import { cn } from "@/lib/utils";
import { term, useTerminology } from "@/lib/terminology";
import { useAgent, useUpdateAgent } from "@/hooks/useAgents";
import { useAgentRuns } from "@/services/agentRuns";
import { useCreateConversation } from "@/hooks/useChat";
import { useAuth } from "@/contexts/AuthContext";
import { parseSolutionFrom } from "@/lib/solution-back-nav";

type Tab = "overview" | "runs" | "settings";

export function AgentDetailPage() {
	const shortDesktop = useMediaQuery(
		"(min-width: 1024px) and (max-height: 700px)",
	);
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const terminology = useTerminology();

	const isCreate = !id || id === "new";
	const agentId = isCreate ? undefined : id;
	const {
		data: agent,
		isLoading: agentLoading,
		isError: agentError,
		isFetching: agentFetching,
		refetch: refetchAgent,
	} = useAgent(agentId);
	const { data: runsList } = useAgentRuns({
		agentId: agentId ?? "",
		limit: 1,
	});
	const runCount = (runsList as { total?: number } | undefined)?.total ?? 0;

	// Tab state lives in the URL (`?tab=`) so deep links — e.g. "Review failed
	// runs" on the backfill card — switch the tab after mount without a full
	// reload. Falling back to "settings" during create or "overview" otherwise.
	const [searchParams, setSearchParams] = useSearchParams();
	const fromSolution = parseSolutionFrom(searchParams.toString());
	const tabParam = searchParams.get("tab");
	const tab: Tab = isCreate
		? "settings"
		: tabParam === "runs" || tabParam === "settings"
			? tabParam
			: "overview";

	const [settingsVisited, setSettingsVisited] = useState(tab === "settings");
	if (tab === "settings" && !settingsVisited) setSettingsVisited(true);

	function handleTabChange(next: Tab) {
		const params = new URLSearchParams(searchParams);
		if (next === "overview") {
			params.delete("tab");
		} else {
			params.set("tab", next);
		}
		if (next !== "runs") params.delete("summary");
		setSearchParams(params, { replace: true });
	}

	const updateAgent = useUpdateAgent();
	const createConversation = useCreateConversation();
	const { isPlatformAdmin } = useAuth();
	const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
	const actionBusy = useRef(false);
	const [actionFailure, setActionFailure] = useState<{
		id: string;
		kind: "chat" | "status";
		active?: boolean;
	} | null>(null);
	const actionPending = updateAgent.isPending || createConversation.isPending;
	function setActive(active: boolean) {
		if (!agent?.id || agent.is_solution_managed || actionBusy.current)
			return;
		actionBusy.current = true;
		setActionFailure(null);
		updateAgent.mutate(
			{
				params: { path: { agent_id: agent.id } },
				body: { is_active: active, clear_roles: false },
			},
			{
				onError: () =>
					setActionFailure({ id: agent.id, kind: "status", active }),
				onSettled: () => {
					actionBusy.current = false;
				},
			},
		);
	}

	function handleCreated(newId: string) {
		navigate(`/agents/${newId}`);
	}

	const hasChat = (agent?.channels ?? []).includes("chat");
	const isActive = agent?.is_active ?? true;

	function handleStartChat() {
		if (!agent?.id || !isActive || actionBusy.current) return;
		actionBusy.current = true;
		setActionFailure(null);
		createConversation.mutate(
			// eslint-disable-next-line @typescript-eslint/no-explicit-any -- body type lags OpenAPI regen
			{ body: { channel: "chat", agent_id: agent.id } as any },
			{
				onSuccess: (conv) => {
					navigate(`/chat/${conv.id}`);
				},
				onSettled: () => {
					actionBusy.current = false;
				},
				onError: () => {
					setActionFailure({ id: agent.id, kind: "chat" });
				},
			},
		);
	}

	if (!isCreate && !agent) {
		return (
			<div className="mx-auto flex w-full max-w-[1400px] min-w-0 flex-col gap-5">
				<Link
					to={fromSolution ? `/solutions/${fromSolution}` : "/agents"}
					className="inline-flex min-h-11 items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
				>
					<ArrowLeft className="size-4" />
					{fromSolution ? "Back to Solution" : "Back to agents"}
				</Link>
				<h1 className={TYPE_PAGE_TITLE}>Agent</h1>
				{agentError ? (
					<FleetReadError
						resource="agent details"
						cached={false}
						pending={agentFetching}
						onRetry={() => void refetchAgent()}
					/>
				) : agentLoading ? (
					<div
						role="status"
						aria-label="Loading agent"
						className="space-y-4"
					>
						<Skeleton className="h-10 w-2/3" />
						<Skeleton className="h-64 w-full" />
					</div>
				) : (
					<p className="text-sm text-muted-foreground">
						Agent not found.
					</p>
				)}
			</div>
		);
	}

	return (
		<PageWorkspace
			className={cn(
				"mx-auto flex w-full max-w-[1400px] min-w-0 flex-col gap-5",
			)}
		>
			<div className="shrink-0 space-y-6">
				{agentError && !isCreate ? (
					<FleetReadError
						resource="agent details"
						cached={!!agent}
						pending={agentFetching}
						onRetry={() => void refetchAgent()}
					/>
				) : null}
				{/* Breadcrumb */}
				<div
					className={cn(
						"flex min-w-0 flex-wrap items-center gap-1.5 text-[13px]",
						TONE_MUTED,
					)}
				>
					<Link
						to={
							fromSolution
								? `/solutions/${fromSolution}`
								: "/agents"
						}
						className="inline-flex min-h-11 items-center gap-1 hover:text-foreground sm:min-h-0"
					>
						<ArrowLeft className="h-3 w-3" />{" "}
						{fromSolution
							? "Back to Solution"
							: term(terminology, "agent", "plural")}
					</Link>
					{!isCreate && agent ? (
						<>
							<span>/</span>
							<span className="min-w-0 [overflow-wrap:anywhere]">
								{agent.name}
							</span>
						</>
					) : null}
				</div>

				{/* Header */}
				<div className="flex flex-wrap items-start justify-between gap-4">
					<div className="flex items-start gap-3 min-w-0 flex-1">
						{!isCreate && agent ? (
							agent.is_solution_managed ? (
								<EntityLogo
									entityType="agent"
									entityId={agent.id}
									logo={agent.logo_url}
									size={48}
									fallback={<Bot className="h-5 w-5" />}
								/>
							) : (
								<AgentLogoEditor
									agentId={agent.id}
									logoUrl={agent.logo_url}
								/>
							)
						) : null}
						<div className="min-w-0 flex-1">
							<h1
								className={cn(
									"flex flex-wrap items-center gap-2.5",
									TYPE_PAGE_TITLE,
								)}
							>
								<span className="min-w-0 [overflow-wrap:anywhere]">
									{isCreate
										? "New agent"
										: (agent?.name ?? "Unknown agent")}
								</span>
								{!isCreate && agent ? (
									isActive ? (
										<span className={PILL_ACTIVE}>
											Active
										</span>
									) : (
										<Badge
											variant="secondary"
											className="text-[11px]"
										>
											Paused
										</Badge>
									)
								) : null}
							</h1>
							{!isCreate && agent?.description ? (
								<p
									className={cn(
										"mt-1 [overflow-wrap:anywhere]",
										TYPE_BODY,
										TONE_MUTED,
									)}
								>
									{agent.description}
								</p>
							) : null}
						</div>
					</div>
					{!isCreate && agent ? (
						<div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
								<Button asChild variant="outline" size="sm"><Link to={`/agents/${agent.id}/quality?tab=tests`}>Open Workbench</Link></Button>
							{hasChat ? (
								<TooltipProvider>
									<Tooltip>
										<TooltipTrigger asChild>
											<span>
												<Button
													variant="outline"
													size="sm"
													disabled={
														!isActive ||
														actionPending
													}
													onClick={handleStartChat}
													data-testid="start-chat-button"
													className="min-h-11 sm:min-h-0"
												>
													{createConversation.isPending ? (
														<Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
													) : (
														<MessageSquare className="h-3.5 w-3.5" />
													)}
													Start chat
												</Button>
											</span>
										</TooltipTrigger>
										<TooltipContent>
											{isActive
												? `Open a chat session with this ${term(terminology, "agent", "singularLower")}`
												: `${term(terminology, "agent", "singular")} is paused`}
										</TooltipContent>
									</Tooltip>
								</TooltipProvider>
							) : null}
							<Button
								variant="outline"
								size="sm"
								className="min-h-11 sm:min-h-0"
								onClick={() => setActive(!isActive)}
								title={
									agent.is_solution_managed
										? "Managed by a Solution"
										: undefined
								}
								disabled={
									actionPending || agent.is_solution_managed
								}
							>
								{isActive ? (
									<>
										<Pause className="h-3.5 w-3.5" /> Pause
									</>
								) : (
									<>
										<PlayCircle className="h-3.5 w-3.5" />{" "}
										Activate
									</>
								)}
							</Button>
							<Button
								variant="outline"
								size="sm"
								className="min-h-11 min-w-11 sm:min-h-0 sm:min-w-0 text-destructive hover:text-destructive hover:bg-destructive/10"
								onClick={() => setConfirmDeleteOpen(true)}
								disabled={
									actionPending || agent.is_solution_managed
								}
								title={
									agent.is_solution_managed
										? "Managed by a Solution"
										: "Delete agent"
								}
								aria-label="Delete agent"
							>
								<Trash2 className="h-3.5 w-3.5" />
							</Button>
							{isPlatformAdmin ? (
								<SummaryBackfillButton
									agentId={agent.id ?? undefined}
								/>
							) : null}
						</div>
					) : null}
				</div>

				{!isCreate && agent ? (
					<AgentDeleteDialog
						agentId={agent.id}
						name={agent.name}
						open={confirmDeleteOpen}
						onOpenChange={setConfirmDeleteOpen}
						onDeleted={() => navigate("/agents")}
					/>
				) : null}

				<RunActionFeedback
					pending={actionPending}
					failed={!!actionFailure && actionFailure.id === agentId}
					onRetry={() => {
						if (actionFailure?.kind === "chat") handleStartChat();
						else if (actionFailure?.active !== undefined)
							setActive(actionFailure.active);
					}}
					pendingLabel={
						createConversation.isPending
							? "Starting chat…"
							: "Updating agent status…"
					}
					message={
						actionFailure?.kind === "chat"
							? "Could not start the chat. Try again."
							: "Could not update the agent status. Try again."
					}
					retryLabel={
						actionFailure?.kind === "chat"
							? "Retry start chat"
							: "Retry status update"
					}
				/>
				{/* Pill tabs */}
				<PillTabs
					items={[
						{
							value: "overview",
							label: "Overview",
							disabled: isCreate,
						},
						{
							value: "runs",
							label: "Runs",
							count: runCount,
							disabled: isCreate,
						},
						{ value: "settings", label: "Settings" },
					]}
					value={tab}
					onValueChange={(v) => handleTabChange(v as Tab)}
				/>
			</div>

			{/* Tab body */}
			<PageScrollArea
				className={
					tab === "runs" && !shortDesktop
						? "lg:flex lg:flex-col lg:overflow-hidden"
						: "space-y-6"
				}
			>
				{tab === "overview" && !isCreate && agentId ? (
					<AgentOverviewTab agentId={agentId} />
				) : null}
				{tab === "runs" && !isCreate && agentId ? (
					<AgentRunsTab agentId={agentId} />
				) : null}
				{tab === "settings" || settingsVisited ? (
					<div hidden={tab !== "settings"}>
						{isCreate ? (
							<AgentSettingsTab
								key="create"
								mode="create"
								onCreated={handleCreated}
							/>
						) : (
							<AgentSettingsTab
								key={agentId}
								mode="edit"
								agent={agent ?? null}
							/>
						)}
					</div>
				) : null}
			</PageScrollArea>
		</PageWorkspace>
	);
}

export default AgentDetailPage;
