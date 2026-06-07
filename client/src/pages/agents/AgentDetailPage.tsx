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

import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
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
import { LogoDropZone } from "@/components/LogoDropZone";
import { bumpEntityLogo } from "@/components/entityLogoVersions";
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
import { useAgent, useDeleteAgent, useUpdateAgent } from "@/hooks/useAgents";
import { useAgentRuns } from "@/services/agentRuns";
import { useCreateConversation } from "@/hooks/useChat";
import { useAuth } from "@/contexts/AuthContext";
import { parseSolutionFrom } from "@/lib/solution-back-nav";

type Tab = "overview" | "runs" | "settings";

export function AgentDetailPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const terminology = useTerminology();

	const isCreate = !id || id === "new";
	const agentId = isCreate ? undefined : id;
	const { data: agent, isLoading } = useAgent(agentId);
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
	const tab: Tab =
		tabParam === "runs" || tabParam === "settings"
			? tabParam
			: isCreate
				? "settings"
				: "overview";

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
	const deleteAgent = useDeleteAgent();
	const createConversation = useCreateConversation();
	const { isPlatformAdmin } = useAuth();
	const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

	function handleCreated(newId: string) {
		navigate(`/agents/${newId}`);
	}

	const hasChat = (agent?.channels ?? []).includes("chat");
	const isActive = agent?.is_active ?? true;

	function handleStartChat() {
		if (!agent?.id) return;
		createConversation.mutate(
			// eslint-disable-next-line @typescript-eslint/no-explicit-any -- body type lags OpenAPI regen
			{ body: { channel: "chat", agent_id: agent.id } as any },
			{
				onSuccess: (conv) => {
					navigate(`/chat/${conv.id}`);
				},
				onError: () => {
					toast.error("Failed to start chat");
				},
			},
		);
	}

	return (
		<div className="mx-auto flex max-w-[1400px] flex-col gap-5 p-7">
			{/* Breadcrumb */}
			<div
				className={cn(
					"flex items-center gap-1.5 text-[13px]",
					TONE_MUTED,
				)}
			>
				<Link
					to={fromSolution ? `/solutions/${fromSolution}` : "/agents"}
					className="inline-flex items-center gap-1 hover:text-foreground"
				>
					<ArrowLeft className="h-3 w-3" />{" "}
					{fromSolution
						? "Back to Solution"
						: term(terminology, "agent", "plural")}
				</Link>
				{!isCreate && agent ? (
					<>
						<span>/</span>
						<span>{agent.name}</span>
					</>
				) : null}
			</div>

			{/* Header */}
			<div className="flex flex-wrap items-start justify-between gap-4">
				<div className="flex items-start gap-3 min-w-0 flex-1">
					{!isCreate && agent ? (
						<LogoDropZone
							uploadUrl={`/api/agents/${agent.id}/logo`}
							deleteUrl={`/api/agents/${agent.id}/logo`}
							previewUrl={`/api/agents/${agent.id}/logo`}
							fallback={<Bot className="h-5 w-5" />}
							size={48}
							onChange={() =>
								bumpEntityLogo("agent", agent.id ?? "")
							}
						/>
					) : null}
					<div className="min-w-0 flex-1">
						<h1 className={cn("flex items-center gap-2.5", TYPE_PAGE_TITLE)}>
							<span className="truncate">
								{isCreate
									? "New agent"
									: isLoading
										? "Loading…"
										: (agent?.name ?? "Unknown agent")}
							</span>
							{!isCreate && agent ? (
								isActive ? (
									<span className={PILL_ACTIVE}>Active</span>
								) : (
									<Badge variant="secondary" className="text-[11px]">
										Paused
									</Badge>
								)
							) : null}
						</h1>
						{!isCreate && agent?.description ? (
							<p className={cn("mt-1 line-clamp-2", TYPE_BODY, TONE_MUTED)}>
								{agent.description}
							</p>
						) : null}
					</div>
				</div>
				{!isCreate && agent ? (
					<div className="flex items-center gap-2">
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
													createConversation.isPending
												}
												onClick={handleStartChat}
												data-testid="start-chat-button"
											>
												{createConversation.isPending ? (
													<Loader2 className="h-3.5 w-3.5 animate-spin" />
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
							onClick={() =>
								updateAgent.mutate({
									params: { path: { agent_id: agent.id ?? "" } },
									body: { is_active: !isActive, clear_roles: false },
								})
							}
						>
							{isActive ? (
								<>
									<Pause className="h-3.5 w-3.5" /> Pause
								</>
							) : (
								<>
									<PlayCircle className="h-3.5 w-3.5" /> Activate
								</>
							)}
						</Button>
						<Button
							variant="outline"
							size="sm"
							className="text-destructive hover:text-destructive hover:bg-destructive/10"
							onClick={() => setConfirmDeleteOpen(true)}
							disabled={deleteAgent.isPending}
							title="Delete agent"
							aria-label="Delete agent"
						>
							<Trash2 className="h-3.5 w-3.5" />
						</Button>
						{isPlatformAdmin ? (
							<SummaryBackfillButton agentId={agent.id ?? undefined} />
						) : null}
					</div>
				) : null}
			</div>

			{!isCreate && agent ? (
				<AlertDialog
					open={confirmDeleteOpen}
					onOpenChange={setConfirmDeleteOpen}
				>
					<AlertDialogContent>
						<AlertDialogHeader>
							<AlertDialogTitle>Delete agent?</AlertDialogTitle>
							<AlertDialogDescription>
								This will delete <strong>{agent.name}</strong>{" "}
								and its run history. This action cannot be
								undone.
							</AlertDialogDescription>
						</AlertDialogHeader>
						<AlertDialogFooter>
							<AlertDialogCancel>Cancel</AlertDialogCancel>
							<AlertDialogAction
								className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
								onClick={async () => {
									await deleteAgent.mutateAsync({
										params: {
											path: { agent_id: agent.id ?? "" },
										},
									});
									setConfirmDeleteOpen(false);
									navigate("/agents");
								}}
							>
								Delete
							</AlertDialogAction>
						</AlertDialogFooter>
					</AlertDialogContent>
				</AlertDialog>
			) : null}

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

			{/* Tab body */}
			{tab === "overview" && !isCreate && agentId ? (
				<AgentOverviewTab agentId={agentId} />
			) : null}
			{tab === "runs" && !isCreate && agentId ? (
				<AgentRunsTab agentId={agentId} />
			) : null}
			{tab === "settings" ? (
				isCreate ? (
					<AgentSettingsTab mode="create" onCreated={handleCreated} />
				) : isLoading ? (
					<Skeleton className="h-64 w-full" />
				) : (
					<AgentSettingsTab mode="edit" agent={agent ?? null} />
				)
			) : null}
		</div>
	);
}

export default AgentDetailPage;
