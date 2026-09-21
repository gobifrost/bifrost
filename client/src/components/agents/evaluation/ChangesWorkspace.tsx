import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { useAgent, useAgents } from "@/hooks/useAgents";
import { useToolsGrouped } from "@/hooks/useTools";
import { listModelProfiles } from "@/services/aiModels";
import {
	friendlyFieldLabel,
	describeChangeValue,
} from "@/components/agents/evaluation/CandidateEditor";
import { useAgentPlatformUpdates } from "@/hooks/useAgentPlatformUpdates";
import { agentPlatform } from "@/services/agentPlatform";
import { Button } from "@/components/ui/button";
import {
	CandidateEditor,
	CandidatePromotion,
} from "@/components/agents/evaluation/CandidateEditor";
import { ExecutionResults } from "@/components/agents/evaluation/ExecutionResults";
import { MatrixPanel } from "@/components/agents/evaluation/MatrixPanel";
import {
	EvidenceJson,
	PlatformError,
	PlatformStatus,
} from "@/components/agents/evaluation/PlatformEvidence";

/**
 * ChangesWorkspace — candidate, matrix comparison, and explicit apply
 * (Changes tab content).
 *
 * Candidates are immutable evaluation-only snapshots; the matrix pairs
 * every candidate cell with the baseline cell under the same profile.
 * `CandidatePromotion` applies reviewed changes through the normal
 * authorized Agent update with history and a stale guard. A single
 * `execution` deep link still renders through the same results view.
 */
export function ChangesWorkspace({
	agentId,
	suiteId,
	candidateId,
	profileIds,
	matrixId,
	executionId,
	onNavigate,
}: {
	agentId: string;
	suiteId: string;
	candidateId: string;
	profileIds: string[];
	matrixId: string;
	executionId: string;
	onNavigate: (values: Record<string, string | undefined>) => void;
}) {
	const agent = useAgent(agentId);
	const profilesQuery = useQuery({
		queryKey: ["ai", "model-profiles"],
		queryFn: listModelProfiles,
	});
	const toolsGroupedQuery = useToolsGrouped();
	const agentsQuery = useAgents();
	const suite = useQuery({
		queryKey: ["agent-platform", "suite", suiteId],
		queryFn: () => agentPlatform.suite(suiteId),
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
	const cases = useQuery({
		queryKey: ["agent-platform", "cases", suiteId],
		queryFn: () => agentPlatform.cases(suiteId),
		enabled: !!suiteId,
	});
	const candidateChanges = Object.entries(
		candidate.data?.overlays ?? {},
	).filter(([, value]) => value != null);
	const [editorOpen, setEditorOpen] = useState(false);
	const executionProfileName = execution.data?.profile_id
		? (profilesQuery.data?.find(
				(profile) => profile.id === execution.data?.profile_id,
			)?.name ?? "Profile unavailable")
		: null;
	useAgentPlatformUpdates(
		undefined,
		execution.data?.platform_job_id ?? undefined,
	);
	const names = {
		profileName: (id: unknown) =>
			typeof id === "string"
				? (profilesQuery.data?.find((profile) => profile.id === id)
						?.name ?? id)
				: "Default assignment",
		toolName: (id: unknown) =>
			typeof id === "string"
				? (toolsGroupedQuery.data?.workflow.find(
						(tool) => tool.id === id,
					)?.name ?? id)
				: String(id),
		delegateName: (id: unknown) =>
			typeof id === "string"
				? ((agentsQuery.data ?? []).find((item) => item.id === id)
						?.name ?? id)
				: String(id),
	};
	return (
		<div className="space-y-6">
			<PlatformError
				error={agent.error}
				retry={() => void agent.refetch()}
			/>
			{!suiteId && (
				<div className="rounded-lg border border-dashed p-8 text-center">
					<h3 className="text-lg font-semibold">
						Select a suite first
					</h3>
					<p className="mx-auto mt-2 max-w-prose text-sm text-muted-foreground">
						Comparisons run against a published suite from the
						Tests tab. Proposed changes can be created below at
						any time.
					</p>
				</div>
			)}
			{suiteId && suite.data && suite.data.status !== "published" && (
				<p role="note" className="text-sm text-muted-foreground">
					This suite is still a draft. Publish it from the Tests
					tab before running comparisons.
				</p>
			)}
		<section aria-label="Proposed changes">
		<div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
			<h3 className="text-lg font-semibold">Proposed changes</h3>
			{candidateId && candidate.data && (
				<span className="min-w-0 break-words text-sm font-medium">
					{candidate.data.name}
				</span>
			)}
			{candidateChanges.length > 0 && (
				<ul className="flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
					{candidateChanges.map(([key, value]) => (
						<li key={key} className="min-w-0 break-words">
							{key === "system_prompt" ? (
								"Prompt updated"
							) : (
								<>
									{friendlyFieldLabel(key)}:{" "}
									{String(
										describeChangeValue(key, value, names) ??
											"",
									)}
								</>
							)}
						</li>
					))}
				</ul>
			)}
			<div className="flex flex-wrap items-center gap-2">
				{candidateId && (
					<Button
						size="sm"
						variant="ghost"
						onClick={() =>
							onNavigate({
								candidate: undefined,
								execution: undefined,
							})
						}
					>
						Use live agent only
					</Button>
				)}
				{(!candidateId || candidate.data) && (
					<Button
						size="sm"
						variant="outline"
						onClick={() => {
							if (candidateId) {
								setEditorOpen(true);
								onNavigate({
									candidate: undefined,
									execution: undefined,
								});
							} else {
								setEditorOpen((value) => !value);
							}
						}}
						aria-expanded={candidateId ? undefined : editorOpen}
					>
						{!candidateId && editorOpen
							? "Close editor"
							: "Create proposed changes"}
					</Button>
				)}
			</div>
		</div>
		<PlatformError
			error={candidate.error}
			retry={() => void candidate.refetch()}
		/>
		{candidateId && candidate.isPending && (
			<p role="status" className="mt-2 text-sm text-muted-foreground">
				Loading proposed changes…
			</p>
		)}
		{candidateId && candidate.data && candidateChanges.length === 0 && (
			<p className="mt-2 text-sm text-muted-foreground">
				No changes from the live agent.
			</p>
		)}
		{candidateId && candidate.data && (
			<details className="mt-2">
				<summary className="w-fit cursor-pointer text-sm text-muted-foreground">
					Snapshot internals
				</summary>
				<div className="mt-2 space-y-3">
					<p className="break-all font-mono text-xs text-muted-foreground">
						Snapshot SHA-256: {candidate.data.snapshot_hash}
					</p>
					<EvidenceJson
						label="Saved candidate details"
						value={Object.fromEntries(candidateChanges)}
					/>
					<EvidenceJson
						label="Effective candidate snapshot"
						value={candidate.data.snapshot}
					/>
				</div>
			</details>
		)}
		{!candidateId && !editorOpen && (
			<p className="mt-2 text-sm text-muted-foreground">
				No proposed changes yet. Create a set to compare against
				the live agent. The live agent stays unchanged until you
				review and apply.
			</p>
		)}
		{!candidateId && editorOpen && agent.data && (
			<div className="mt-3">
				<CandidateEditor
					key={agent.data.updated_at}
					agent={agent.data}
					onCreated={(data) => {
						setEditorOpen(false);
						onNavigate({
							candidate: data.id,
							execution: undefined,
						});
					}}
				/>
			</div>
		)}
	</section>
			{suiteId && suite.data && suite.data.status === "published" && agent.data && (
				<MatrixPanel
					agentId={agentId}
					suiteId={suiteId}
					candidateId={candidateId}
					profileIds={profileIds}
					matrixId={matrixId}
					onProfilesChange={(profiles) =>
						onNavigate({
							profiles: profiles.length
								? profiles.join(",")
								: undefined,
						})
					}
					onMatrix={(id) =>
						onNavigate({ matrix: id, execution: undefined })
					}
				/>
			)}
			{executionId && (
				<section className="space-y-4 rounded-lg border p-4" aria-label="Saved test run">
					<div className="flex flex-wrap items-center gap-3">
						<PlatformStatus
							status={execution.data?.status ?? "queued"}
						/>
						<span className="text-sm">
							{execution.data?.completed_cases ?? 0} /{" "}
							{execution.data?.total_cases ?? 0} completed ·{" "}
							{execution.data?.passed_cases ?? 0} passed ·{" "}
							{execution.data?.failed_cases ?? 0} failed
						</span>
						{execution.data?.candidate_id ? (
							<span className="text-sm text-muted-foreground">
								Proposed changes
							</span>
						) : (
							<span className="text-sm text-muted-foreground">
								Live agent
							</span>
						)}
						{executionProfileName && (
							<span className="text-sm text-muted-foreground">
								· {executionProfileName}
							</span>
						)}
					</div>
					<PlatformError
						error={results.error}
						retry={() => void results.refetch()}
					/>
					{results.isPending ? (
						<p role="status">Loading test results…</p>
					) : (
						<ExecutionResults
							results={results.data ?? []}
							cases={cases.data ?? []}
							agentId={agentId}
						/>
					)}
				</section>
			)}
			{candidateId && candidate.data && (
				<CandidatePromotion
					candidate={candidate.data}
					agentId={agentId}
					onApplied={() => {
						void agent.refetch();
						toast.success(
							"Proposed changes applied to the live agent",
						);
					}}
				/>
			)}
		</div>
	);
}
