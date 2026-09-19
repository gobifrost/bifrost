import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { ModelProfileSelector } from "@/components/ai/ModelProfileSelector";
import { agentPlatform } from "@/services/agentPlatform";
import { apiClient } from "@/lib/api-client";
import { EvidenceJson, PlatformError } from "./PlatformEvidence";
import type { components } from "@/lib/v1";

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
	const [advanced, setAdvanced] = useState("{}");
	const create = useMutation({
		mutationFn: () => {
			const overlays: components["schemas"]["CandidateOverlay"] =
				JSON.parse(advanced);
			if (prompt !== agent.system_prompt) overlays.system_prompt = prompt;
			if (profile !== (agent.llm_profile_id ?? ""))
				overlays.llm_profile_id = profile || null;
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
						Live Agent · {agent.name}
					</h3>
					<p className="mb-2 text-xs text-muted-foreground">
						Current production prompt
					</p>
					<EvidenceJson
						label="Live Agent prompt"
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
					Tool, delegate and limit overrides
				</summary>
				<div className="mt-3">
					<Label htmlFor="candidate-overlays">
						Additional overrides (JSON)
					</Label>
					<Textarea
						id="candidate-overlays"
						rows={6}
						className="font-mono text-xs"
						value={advanced}
						onChange={(event) => setAdvanced(event.target.value)}
					/>
					<p className="mt-2 text-xs text-muted-foreground">
						Supported fields: tool_ids, delegated_agent_ids,
						system_tools, llm_max_tokens, max_iterations,
						max_token_budget, max_run_timeout and output_schema.
						Omitted values inherit the live Agent at creation. Lists
						replace the corresponding grant list.
					</p>
					<EvidenceJson
						label="Current Agent grants and limits"
						value={{
							tool_ids: agent.tool_ids,
							delegated_agent_ids: agent.delegated_agent_ids,
							system_tools: agent.system_tools,
							max_iterations: agent.max_iterations,
							max_token_budget: agent.max_token_budget,
							max_run_timeout: agent.max_run_timeout,
						}}
					/>
				</div>
			</details>
			<PlatformError error={create.error} />
			<Button disabled={create.isPending}>
				{create.isPending
					? "Freezing candidate…"
					: "Create immutable candidate"}
			</Button>
			<p className="text-xs text-muted-foreground">
				This creates an evaluation-only snapshot. The live Agent stays
				unchanged.
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
					"The live Agent changed after review. Review the refreshed diff before applying again.",
				);
			}
			const response = await apiClient.PUT("/api/agents/{agent_id}", {
				params: { path: { agent_id: agentId } },
				body: { ...Object.fromEntries(changes), clear_roles: false },
			});
			if (response.error) throw response.error;
		},
		onSuccess: () => {
			setReview(false);
			onApplied();
		},
	});
	return (
		<section className="space-y-3 border-t pt-5">
			<h3 className="font-semibold">Apply candidate to live Agent</h3>
			<p className="text-sm text-muted-foreground">
				Test success never publishes. Review the exact changes against
				the current production configuration before applying.
			</p>
			{!review ? (
				<Button variant="outline" onClick={() => setReview(true)}>
					Review production diff
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
											{key}
										</h4>
										<div className="grid gap-3 md:grid-cols-2">
											<div>
												<p className="mb-1 text-xs text-muted-foreground">
													Live now
												</p>
												<EvidenceJson
													label={`Live ${key}`}
													value={
														live.data[
															key as keyof Agent
														]
													}
												/>
											</div>
											<div>
												<p className="mb-1 text-xs text-muted-foreground">
													After apply
												</p>
												<EvidenceJson
													label={`Candidate ${key}`}
													value={value}
												/>
											</div>
										</div>
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
										: "Apply these changes to live Agent"}
								</Button>
								<Button
									variant="outline"
									onClick={() => setReview(false)}
								>
									Cancel
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
