import { Navigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { agentPlatform } from "@/services/agentPlatform";
import { PlatformError } from "@/components/agents/evaluation/PlatformEvidence";

/** Resolve the canonical PlatformJob notification URL into the Agent workspace. */
export function EvaluationExecutionLink() {
	const { executionId = "" } = useParams();
	const execution = useQuery({
		queryKey: ["agent-platform", "execution", executionId],
		queryFn: () => agentPlatform.execution(executionId),
	});
	if (execution.error)
		return (
			<PlatformError
				error={execution.error}
				retry={() => void execution.refetch()}
			/>
		);
	if (!execution.data)
		return <p role="status">Loading evaluation execution…</p>;
	if (!execution.data.baseline_agent_id)
		return (
			<p role="alert">
				The baseline Agent for this execution is no longer available.
			</p>
		);
	const params = new URLSearchParams({
		suite: execution.data.suite_id,
		execution: executionId,
		tab: "results",
	});
	if (execution.data.candidate_id)
		params.set("candidate", execution.data.candidate_id);
	return (
		<Navigate
			replace
			to={`/agents/${execution.data.baseline_agent_id}/studio?${params}`}
		/>
	);
}
