import { $api } from "@/lib/api-client";

/** Input controls for a runnable workflow, without requiring catalog read access. */
export function useWorkflowParameters(workflowId: string | null) {
	return $api.useQuery(
		"get",
		"/api/workflows/{workflow_id}/parameters",
		{ params: { path: { workflow_id: workflowId! } } },
		{ enabled: !!workflowId },
	);
}
