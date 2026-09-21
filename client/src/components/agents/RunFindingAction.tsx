import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { RunActionFeedback } from "./RunActionFeedback";
import { Button } from "@/components/ui/button";
import { agentPlatform } from "@/services/agentPlatform";
import type { components } from "@/lib/v1";

type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];
type Finding = components["schemas"]["FindingPublic"];

export function RunFindingAction({
	run,
	note,
}: {
	run: AgentRunDetail;
	note: string;
}) {
	const queryClient = useQueryClient();
	const agentId = run.agent_id;
	const findings = useQuery({
		queryKey: ["agent-platform", "findings", agentId],
		queryFn: () => agentPlatform.findings(agentId!),
		enabled: !!agentId,
		retry: false,
	});
	const create = useMutation({
		mutationFn: () =>
			agentPlatform.createFinding({
				agent_id: agentId!,
				description: findingDescription(run, note),
				expected_behavior: null,
				source_kind: "run",
				finding_kind: "problem",
				source_run_id: run.id,
				source_sequence: null,
				external_ref: null,
			}),
		onSuccess: (finding) => {
			queryClient.setQueryData<Finding[]>(
				["agent-platform", "findings", agentId],
				(previous) => {
					if (!previous) return [finding];
					if (previous.some((item) => item.id === finding.id))
						return previous;
					return [finding, ...previous];
				},
			);
			void queryClient.invalidateQueries({
				queryKey: ["agent-platform", "findings"],
			});
			void queryClient.invalidateQueries({
				queryKey: ["agent-quality", "findings"],
			});
		},
	});
	const existing =
		create.data ??
		(findings.data ?? []).find(
			(finding) =>
				finding.source_kind === "run" &&
				finding.source_run_id === run.id,
		);

	if (!agentId) return null;
	if (existing) {
		return (
			<Button asChild variant="outline" className="min-h-11">
				<Link
					to={`/agents/${agentId}/quality?collection=findings&selected=findings:${existing.id}`}
				>
					Open Finding
				</Link>
			</Button>
		);
	}

	return (
		<div className="space-y-3">
			<Button
				type="button"
				variant="outline"
				className="min-h-11"
				disabled={create.isPending || findings.isLoading}
				onClick={() => create.mutate()}
			>
				{create.isPending ? "Creating Finding…" : "Create Finding"}
			</Button>
			<RunActionFeedback
				pending={false}
				failed={create.isError}
				onRetry={() => create.mutate()}
				message="Could not create Finding. Your review note is still here."
				retryLabel="Retry Create Finding"
			/>
		</div>
	);
}

function findingDescription(run: AgentRunDetail, note: string) {
	return (
		note.trim() ||
		run.verdict_note?.trim() ||
		run.did?.trim() ||
		run.asked?.trim() ||
		`Problem observed in run ${run.id.slice(0, 8)}.`
	);
}
