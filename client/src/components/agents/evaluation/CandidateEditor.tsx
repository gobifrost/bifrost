import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { MultiCombobox } from "@/components/ui/multi-combobox";
import { ModelProfileSelector } from "@/components/ai/ModelProfileSelector";
import { agentPlatform } from "@/services/agentPlatform";
import { apiClient } from "@/lib/api-client";
import { useAgents } from "@/hooks/useAgents";
import { useSystemTools, useToolsGrouped } from "@/hooks/useTools";
import { PromptDiffViewer } from "@/components/agents/PromptDiffViewer";
import { listModelProfiles } from "@/services/aiModels";
import { EvidenceJson, PlatformError } from "./PlatformEvidence";
import type { components } from "@/lib/v1";

const CHANGE_FIELD_LABELS: Record<string, string> = {
	system_prompt: "Prompt",
	llm_profile_id: "Model profile",
	llm_max_tokens: "Max output tokens",
	tool_ids: "Workflow tools",
	delegated_agent_ids: "Delegated agents",
	system_tools: "System tools",
	max_iterations: "Max iterations",
	max_token_budget: "Max token budget",
	max_run_timeout: "Max run timeout",
};

export function friendlyFieldLabel(key: string): string {
	return CHANGE_FIELD_LABELS[key] ?? key;
}

export function describeChangeValue(
	key: string,
	value: unknown,
	names: {
		profileName: (id: unknown) => string;
		toolName: (id: unknown) => string;
		delegateName: (id: unknown) => string;
	},
): unknown {
	if (key === "llm_profile_id") return names.profileName(value);
	if (key === "tool_ids")
		return Array.isArray(value)
			? value.map((id) => names.toolName(id))
			: value;
	if (key === "delegated_agent_ids")
		return Array.isArray(value)
			? value.map((id) => names.delegateName(id))
			: value;
	return value;
}

type Agent = components["schemas"]["AgentPublic"];
type Candidate = components["schemas"]["CandidatePublic"];
export function CandidateEditor({
	agent,
	onCreated,
}: {
	agent: Agent;
	onCreated: (candidate: Candidate) => void;
}) {
	const [name, setName] = useState("");
	const [prompt, setPrompt] = useState(agent.system_prompt);
	const [profile, setProfile] = useState(agent.llm_profile_id ?? "");
	const [toolIds, setToolIds] = useState<string[]>(agent.tool_ids ?? []);
	const [delegateIds, setDelegateIds] = useState<string[]>(
		agent.delegated_agent_ids ?? [],
	);
	const [systemTools, setSystemTools] = useState<string[]>(
		agent.system_tools ?? [],
	);
	const [advanced, setAdvanced] = useState("{}");
	const toolsGrouped = useToolsGrouped();
	const systemToolsQuery = useSystemTools();
	const agentsQuery = useAgents();
	const create = useMutation({
		mutationFn: () => {
			const overlays: components["schemas"]["CandidateOverlay"] =
				JSON.parse(advanced);
			if (prompt !== agent.system_prompt) overlays.system_prompt = prompt;
			if (profile !== (agent.llm_profile_id ?? ""))
				overlays.llm_profile_id = profile || null;
			// Only send lists that differ: omitted values inherit the live
			// Agent, and identical lists would render as spurious diffs.
			const sameIds = (left: string[] | null | undefined, right: string[] | null | undefined) =>
				JSON.stringify([...(left ?? [])].sort()) ===
				JSON.stringify([...(right ?? [])].sort());
			if (!sameIds(toolIds, agent.tool_ids)) overlays.tool_ids = toolIds;
			if (!sameIds(delegateIds, agent.delegated_agent_ids))
				overlays.delegated_agent_ids = delegateIds;
			if (!sameIds(systemTools, agent.system_tools))
				overlays.system_tools = systemTools;
			return agentPlatform.createCandidate({
				base_agent_id: agent.id,
				name: name || undefined,
				overlays,
				organization_id: agent.organization_id,
			});
		},
		onSuccess: onCreated,
	});
	return (
		<form
			className="space-y-4"
			aria-label="Create candidate"
			onSubmit={(event) => {
				event.preventDefault();
				create.mutate();
			}}
		>
			<div>
				<Label htmlFor="candidate-name">Candidate name</Label>
				<Input
					id="candidate-name"
					value={name}
					onChange={(event) => setName(event.target.value)}
					placeholder="Describe the change you want to test"
				/>
			</div>
			<div className="grid gap-4 lg:grid-cols-2">
				<div>
					<h3 className="mb-2 text-sm font-semibold">
						Live agent · {agent.name}
					</h3>
					<p className="mb-2 text-xs text-muted-foreground">
						Current production prompt
					</p>
					<EvidenceJson
						label="Live agent prompt"
						value={agent.system_prompt}
					/>
					<p className="mt-2 text-xs text-muted-foreground">
						Model profile:{" "}
						{agent.llm_profile_id ?? "Default assignment"}
					</p>
				</div>
				<div className="space-y-3">
					<Label htmlFor="candidate-prompt">
						Candidate prompt · evaluation only
					</Label>
					<Textarea
						id="candidate-prompt"
						rows={8}
						value={prompt}
						onChange={(event) => setPrompt(event.target.value)}
					/>
					<ModelProfileSelector
						id="candidate-profile"
						label="Candidate model profile"
						value={profile || null}
						onValueChange={setProfile}
					/>
				</div>
			</div>
			<details>
				<summary className="cursor-pointer text-sm font-medium">
					Tools, delegates and model profile
				</summary>
				<div className="mt-3 space-y-4">
					<div>
						<Label>Workflow tools</Label>
						<MultiCombobox
							options={(toolsGrouped.data?.workflow ?? []).map(
								(tool) => ({
									value: tool.id,
									label: tool.name,
								}),
							)}
							value={toolIds}
							onValueChange={setToolIds}
							placeholder="Select workflow tools…"
							emptyText="No workflow tools available."
							isLoading={toolsGrouped.isPending}
						/>
						<p className="mt-1 text-xs text-muted-foreground">
							Replaces the live agent&apos;s tool list for this
							candidate.
						</p>
					</div>
					<div>
						<Label>Delegated agents</Label>
						<MultiCombobox
							options={(agentsQuery.data ?? [])
								.filter(
									(item: { id: string | null }) =>
										item.id !== null &&
										item.id !== agent.id,
								)
								.map((item: { id: string; name: string }) => ({
									value: item.id,
									label: item.name,
								}))}
							value={delegateIds}
							onValueChange={setDelegateIds}
							placeholder="Select delegated agents…"
							emptyText="No other agents available."
							isLoading={agentsQuery.isPending}
						/>
					</div>
					<div>
						<Label>System tools</Label>
						<MultiCombobox
							options={(
								systemToolsQuery.data?.tools ?? []
							).map((tool: { name: string }) => ({
								value: tool.name,
								label: tool.name,
							}))}
							value={systemTools}
							onValueChange={setSystemTools}
							placeholder="Select system tools…"
							emptyText="No system tools available."
							isLoading={systemToolsQuery.isPending}
						/>
					</div>
				</div>
			</details>
			<details>
				<summary className="cursor-pointer text-sm font-medium">
					Advanced overrides (JSON)
				</summary>
				<div className="mt-3">
					<Label htmlFor="candidate-overlays">
						Rare settings (JSON)
					</Label>
					<Textarea
						id="candidate-overlays"
						rows={6}
						className="font-mono text-xs"
						value={advanced}
						onChange={(event) => setAdvanced(event.target.value)}
					/>
					<p className="mt-2 text-xs text-muted-foreground">
						Supported fields: llm_max_tokens, max_iterations,
						max_token_budget, max_run_timeout and output_schema.
						Omitted values inherit the live agent at creation.
					</p>
					<EvidenceJson
						label="Current Agent limits"
						value={{
							max_iterations: agent.max_iterations,
							max_token_budget: agent.max_token_budget,
							max_run_timeout: agent.max_run_timeout,
						}}
					/>
				</div>
			</details>
			<PlatformError error={create.error} />
			<Button disabled={create.isPending}>
				{create.isPending ? "Saving…" : "Save proposed changes"}
			</Button>
			<p className="text-xs text-muted-foreground">
				This saves an evaluation-only copy for test runs. The live
				Agent stays unchanged.
			</p>
		</form>
	);
}
export function CandidatePromotion({
	candidate,
	agentId,
	onApplied,
}: {
	candidate: Candidate;
	agentId: string;
	onApplied: () => void;
}) {
	const [review, setReview] = useState(false);
	const [reason, setReason] = useState("");
	const live = useQuery({
		queryKey: ["agent-platform", "promotion", agentId],
		enabled: review,
		queryFn: async () => {
			const response = await apiClient.GET("/api/agents/{agent_id}", {
				params: { path: { agent_id: agentId } },
			});
			if (response.error) throw response.error;
			return response.data;
		},
	});
	const profilesQuery = useQuery({
		queryKey: ["ai", "model-profiles"],
		queryFn: listModelProfiles,
		enabled: review,
	});
	const toolsGroupedQuery = useToolsGrouped();
	const agentsQuery = useAgents();
	const profileName = (id: unknown) =>
		typeof id === "string"
			? (profilesQuery.data?.find((profile) => profile.id === id)
					?.name ?? id)
			: "Default assignment";
	const toolName = (id: unknown) =>
		typeof id === "string"
			? (toolsGroupedQuery.data?.workflow.find((tool) => tool.id === id)
					?.name ?? id)
			: String(id);
	const delegateName = (id: unknown) =>
		typeof id === "string"
			? ((agentsQuery.data ?? []).find((item) => item.id === id)?.name ??
				id)
			: String(id);
	const changes = Object.entries(candidate.overlays ?? {}).filter(
		([, value]) => value != null,
	);
	const unsupported = changes.some(([key]) => key === "output_schema");
	const apply = useMutation({
		mutationFn: async () => {
			const current = await apiClient.GET("/api/agents/{agent_id}", {
				params: { path: { agent_id: agentId } },
			});
			if (current.error) throw current.error;
			if (
				!current.data ||
				changes.some(
					([key]) =>
						JSON.stringify(current.data[key as keyof Agent]) !==
						JSON.stringify(live.data?.[key as keyof Agent]),
				)
			) {
				await live.refetch();
				throw new Error(
					"The live agent changed after review. Review the refreshed diff before applying again.",
				);
			}
			const response = await apiClient.PUT("/api/agents/{agent_id}", {
				params: {
					path: { agent_id: agentId },
					header: live.data?.updated_at
						? { "if-unmodified-since": live.data.updated_at }
						: undefined,
				},
				body: {
					...Object.fromEntries(changes),
					clear_roles: false,
					change_reason: reason.trim() || undefined,
				},
			});
			if (response.error) throw response.error;
		},
		onSuccess: () => {
			setReview(false);
			onApplied();
		},
	});
	return (
		<section className="space-y-3 border-t pt-5" aria-label="Review and apply">
			<h3 className="font-semibold">Review and apply</h3>
			<p className="text-sm text-muted-foreground">
				Test success never publishes. Review the exact changes against
				the current production configuration before applying.
			</p>
			{!review ? (
				<Button variant="outline" onClick={() => setReview(true)}>
					Review and apply
				</Button>
			) : (
				<>
					<PlatformError
						error={live.error}
						retry={() => void live.refetch()}
					/>
					{live.isPending && (
						<p role="status">
							Loading current production configuration…
						</p>
					)}
					{live.data && (
						<>
							<div className="space-y-4">
								{changes.map(([key, value]) => (
									<div key={key}>
										<h4 className="mb-2 text-sm font-medium">
											{friendlyFieldLabel(key)}
										</h4>
										{key === "system_prompt" ? (
											<PromptDiffViewer
												before={String(
													live.data?.system_prompt ??
														"",
												)}
												after={String(value ?? "")}
											/>
										) : (
											<div className="grid gap-3 md:grid-cols-2">
												<div>
													<p className="mb-1 text-xs text-muted-foreground">
														Live now
													</p>
													<EvidenceJson
														label={`Live ${friendlyFieldLabel(key)}`}
														value={describeChangeValue(
															key,
															live.data?.[
																key as keyof Agent
															],
															{
																profileName,
																toolName,
																delegateName,
															},
														)}
													/>
												</div>
												<div>
													<p className="mb-1 text-xs text-muted-foreground">
														After apply
													</p>
													<EvidenceJson
														label={`Candidate ${friendlyFieldLabel(key)}`}
														value={describeChangeValue(
															key,
															value,
															{
																profileName,
																toolName,
																delegateName,
															},
														)}
													/>
												</div>
											</div>
										)}
									</div>
								))}
							</div>
							{!changes.length && (
								<p>No production changes in this candidate.</p>
							)}
							{unsupported && (
								<p role="alert" className="text-sm">
									This candidate has an invocation-only output
									schema. It cannot be applied as an Agent
									setting.
								</p>
							)}
							<div>
								<Label htmlFor="promotion-reason">
									Change reason (recorded in prompt history)
								</Label>
								<Input
									id="promotion-reason"
									value={reason}
									maxLength={500}
									onChange={(event) =>
										setReason(event.target.value)
									}
									placeholder="Why is this change safe to apply?"
								/>
							</div>
							<PlatformError error={apply.error} />
							<div className="flex flex-wrap gap-2">
								<Button
									disabled={
										apply.isPending ||
										unsupported ||
										!changes.length ||
										!!live.data.is_solution_managed
									}
									onClick={() => apply.mutate()}
								>
									{apply.isPending
										? "Applying…"
										: "Apply these changes to live agent"}
								</Button>
						<Button
							variant="outline"
							onClick={() => setReview(false)}
						>
							Close review
						</Button>
							</div>
							{live.data.is_solution_managed && (
								<p className="text-sm">
									This Agent is managed by a Solution. Apply
									configuration changes through its source
									Solution.
								</p>
							)}
						</>
					)}
				</>
			)}
		</section>
	);
}
