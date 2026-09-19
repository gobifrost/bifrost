import { apiClient } from "@/lib/api-client";
import type { components, operations } from "@/lib/v1";

type Schema = components["schemas"];
async function result<T>(
	request: Promise<{ data?: T; error?: unknown }>,
): Promise<T> {
	const { data, error } = await request;
	if (error) throw error;
	if (data === undefined) throw new Error("The server returned no data.");
	return data;
}
export const agentPlatform = {
	snapshot: (run_id: string) =>
		result(
			apiClient.GET("/api/agent-runs/{run_id}/snapshot", {
				params: { path: { run_id } },
			}),
		),
	tree: (run_id: string) =>
		result(
			apiClient.GET("/api/agent-runs/{run_id}/tree", {
				params: { path: { run_id } },
			}),
		),
	timeline: (
		run_id: string,
		query: operations["get_agent_run_timeline_api_agent_runs__run_id__timeline_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET("/api/agent-runs/{run_id}/timeline", {
				params: { path: { run_id }, query },
			}),
		),
	checkpoints: (run_id: string, cursor?: string) =>
		result(
			apiClient.GET("/api/agent-runs/{run_id}/checkpoints", {
				params: { path: { run_id }, query: { cursor } },
			}),
		),
	suites: (offset = 0) =>
		result(
			apiClient.GET("/api/agent-evaluations/suites", {
				params: { query: { offset, limit: 50 } },
			}),
		),
	suite: (suite_id: string) =>
		result(
			apiClient.GET("/api/agent-evaluations/suites/{suite_id}", {
				params: { path: { suite_id } },
			}),
		),
	createSuite: (body: Schema["EvaluationSuiteCreate"]) =>
		result(apiClient.POST("/api/agent-evaluations/suites", { body })),
	updateSuite: (suite_id: string, body: Schema["EvaluationSuiteUpdate"]) =>
		result(
			apiClient.PUT("/api/agent-evaluations/suites/{suite_id}", {
				params: { path: { suite_id } },
				body,
			}),
		),
	publish: (suite_id: string) =>
		result(
			apiClient.POST("/api/agent-evaluations/suites/{suite_id}/publish", {
				params: { path: { suite_id } },
			}),
		),
	cases: (suite_id: string) =>
		result(
			apiClient.GET("/api/agent-evaluations/suites/{suite_id}/cases", {
				params: { path: { suite_id } },
			}),
		),
	createCase: (suite_id: string, body: Schema["EvaluationCaseCreate"]) =>
		result(
			apiClient.POST("/api/agent-evaluations/suites/{suite_id}/cases", {
				params: { path: { suite_id } },
				body,
			}),
		),
	updateCase: (
		suite_id: string,
		case_id: string,
		body: Schema["EvaluationCaseUpdate"],
	) =>
		result(
			apiClient.PUT(
				"/api/agent-evaluations/suites/{suite_id}/cases/{case_id}",
				{ params: { path: { suite_id, case_id } }, body },
			),
		),
	accept: (suite_id: string, draft_id: string) =>
		result(
			apiClient.POST(
				"/api/agent-evaluations/suites/{suite_id}/cases/accept",
				{
					params: { path: { suite_id } },
					body: { draft_id },
				},
			),
		),
	designer: (suite_id: string, body: Schema["DesignerDraftRequest"]) =>
		result(
			apiClient.POST(
				"/api/agent-evaluations/suites/{suite_id}/designer/drafts",
				{ params: { path: { suite_id } }, body },
			),
		),
	candidate: (candidate_id: string) =>
		result(
			apiClient.GET("/api/agent-evaluations/candidates/{candidate_id}", {
				params: { path: { candidate_id } },
			}),
		),
	createCandidate: (body: Schema["CandidateCreate"]) =>
		result(apiClient.POST("/api/agent-evaluations/candidates", { body })),
	async execute(body: Schema["EvaluationExecutionCreate"]) {
		const response = await apiClient.POST(
			"/api/agent-evaluations/executions",
			{
				body,
			},
		);
		const accepted = await result(Promise.resolve(response));
		const executionId = response.response.headers.get(
			"X-Evaluation-Execution-Id",
		);
		if (!executionId)
			throw new Error(
				"Execution was queued, but its link was missing. Check Notifications before starting another execution.",
			);
		return { ...accepted, executionId };
	},
	execution: (execution_id: string) =>
		result(
			apiClient.GET("/api/agent-evaluations/executions/{execution_id}", {
				params: { path: { execution_id } },
			}),
		),
	results: (execution_id: string) =>
		result(
			apiClient.GET(
				"/api/agent-evaluations/executions/{execution_id}/results",
				{ params: { path: { execution_id } } },
			),
		),
	cancel: (execution_id: string) =>
		result(
			apiClient.POST(
				"/api/agent-evaluations/executions/{execution_id}/cancel",
				{ params: { path: { execution_id } } },
			),
		),
};
