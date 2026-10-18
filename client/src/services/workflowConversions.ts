import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type WorkflowConversionAnalyzeRequest =
	components["schemas"]["WorkflowConversionAnalyzeRequest"];
export type WorkflowConversionReport =
	components["schemas"]["WorkflowConversionReport"];
export type WorkflowConversionApplyRequest =
	components["schemas"]["WorkflowConversionApplyRequest"];
export type WorkflowConversionCutoverRequest =
	components["schemas"]["WorkflowConversionCutoverRequest"];
export type PlatformJobAccepted = components["schemas"]["PlatformJobAccepted"];

function responseError(action: string): Error {
	return new Error(`Could not ${action}. Try again.`);
}

export async function analyzeWorkflowConversions(
	body: WorkflowConversionAnalyzeRequest,
): Promise<PlatformJobAccepted> {
	const { data, error } = await apiClient.POST("/api/workflow-conversions", {
		body,
	});
	if (error || !data) throw responseError("start the analysis");
	return data;
}

export async function getWorkflowConversionReport(
	jobId: string,
	options: { offset?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<WorkflowConversionReport> {
	const { data, error } = await apiClient.GET(
		"/api/workflow-conversions/{job_id}/report",
		{
			params: {
				path: { job_id: jobId },
				query: {
					offset: options.offset ?? 0,
					limit: options.limit ?? 25,
				},
			},
			signal: options.signal,
		},
	);
	if (error || !data) throw responseError("load the conversion report");
	return data;
}

export async function applyWorkflowConversion(
	jobId: string,
	body: WorkflowConversionApplyRequest,
): Promise<PlatformJobAccepted> {
	const { data, error } = await apiClient.POST(
		"/api/workflow-conversions/{job_id}/apply",
		{ params: { path: { job_id: jobId } }, body },
	);
	if (error || !data) throw responseError("apply the reviewed changes");
	return data;
}

export async function cutOverWorkflowConversions(
	jobId: string,
	body: WorkflowConversionCutoverRequest,
): Promise<PlatformJobAccepted> {
	const { data, error } = await apiClient.POST(
		"/api/workflow-conversions/{job_id}/cutover",
		{ params: { path: { job_id: jobId } }, body },
	);
	if (error || !data) throw responseError("change the platform default");
	return data;
}
