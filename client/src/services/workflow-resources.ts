import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type WorkflowResourceReport =
	components["schemas"]["WorkflowResourceReport"];
export type WorkflowResourceRun = components["schemas"]["WorkflowResourceRun"];
export type WorkflowResourceWorkflow =
	components["schemas"]["WorkflowResourceWorkflow"];
export type WorkflowResourceStatus = components["schemas"]["ExecutionStatus"];

export interface WorkflowResourceFilters {
	startedAfter: string;
	startedBefore: string;
	view: "runs" | "workflows";
	sort: "cpu" | "elapsed" | "memory" | "ai" | "started";
	page: number;
	pageSize: number;
	orgId?: string;
	workflowId?: string;
	workflow?: string;
	status?: WorkflowResourceStatus;
}

export function useWorkflowResourceReport(filters: WorkflowResourceFilters) {
	return $api.useQuery("get", "/api/reports/workflow-resources", {
		params: {
			query: {
				started_after: filters.startedAfter,
				started_before: filters.startedBefore,
				view: filters.view,
				sort: filters.sort,
				page: filters.page,
				page_size: filters.pageSize,
				...(filters.orgId ? { org_id: filters.orgId } : {}),
				...(filters.workflowId
					? { workflow_id: filters.workflowId }
					: {}),
				...(filters.workflow ? { workflow: filters.workflow } : {}),
				...(filters.status ? { status: filters.status } : {}),
			},
		},
	});
}
