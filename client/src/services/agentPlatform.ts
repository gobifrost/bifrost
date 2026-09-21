import { apiClient } from "@/lib/api-client";
import type { components, operations } from "@/lib/v1";

type Schema = components["schemas"];
type AdmissionResult<TAccepted extends object, Identifier extends string> =
	TAccepted & Record<Identifier, string>;

async function result<T>(
	request: Promise<{ data?: T; error?: unknown }>,
): Promise<T> {
	const { data, error } = await request;
	if (error) throw error;
	if (data === undefined) throw new Error("The server returned no data.");
	return data;
}

async function admitted<TAccepted extends object, Identifier extends string>(
	request: Promise<{
		data?: TAccepted;
		error?: unknown;
		response: Response;
	}>,
	header: string,
	identifier: Identifier,
	message: string,
): Promise<AdmissionResult<TAccepted, Identifier>> {
	const response = await request;
	const accepted = await result(Promise.resolve(response));
	const id = response.response.headers.get(header);
	if (!id) throw new Error(message);
	return { ...accepted, [identifier]: id } as AdmissionResult<
		TAccepted,
		Identifier
	>;
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
		return admitted(
			apiClient.POST("/api/agent-evaluations/executions", {
				body,
			}),
			"X-Evaluation-Execution-Id",
			"executionId",
			"Execution was queued, but its link was missing. Check Notifications before starting another execution.",
		);
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
	executionUsage: (
		execution_id: string,
		query?: operations["get_execution_usage_api_agent_evaluations_executions__execution_id__usage_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET(
				"/api/agent-evaluations/executions/{execution_id}/usage",
				{ params: { path: { execution_id }, query } },
			),
		),
	cancel: (execution_id: string) =>
		result(
			apiClient.POST(
				"/api/agent-evaluations/executions/{execution_id}/cancel",
				{ params: { path: { execution_id } } },
			),
		),
	findings: (agent_id: string) =>
		result(
			apiClient.GET("/api/agent-findings", {
				params: { query: { agent_id } },
			}),
		),
	searchFindings: (
		query?: operations["search_finding_page_api_agent_findings_search_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET("/api/agent-findings/search", {
				params: { query },
			}),
		),
	finding: (finding_id: string) =>
		result(
			apiClient.GET("/api/agent-findings/{finding_id}", {
				params: { path: { finding_id } },
			}),
		),
	createFinding: (body: Schema["FindingCreate"]) =>
		result(apiClient.POST("/api/agent-findings", { body })),
	updateFinding: (
		finding_id: string,
		body: Schema["FindingUpdate"],
	) =>
		result(
			apiClient.PATCH("/api/agent-findings/{finding_id}", {
				params: { path: { finding_id } },
				body,
			}),
		),
	executeBatch: (body: Schema["EvaluationExecutionBatchCreate"]) =>
		result(
			apiClient.POST("/api/agent-evaluations/executions/batch", {
				body,
			}),
		),
	matrix: (matrix_id: string) =>
		result(
			apiClient.GET("/api/agent-evaluations/executions/batch/{matrix_id}", {
				params: { path: { matrix_id } },
			}),
		),
	cancelMatrix: (matrix_id: string) =>
		result(
			apiClient.POST(
				"/api/agent-evaluations/executions/batch/{matrix_id}/cancel",
				{ params: { path: { matrix_id } } },
			),
		),
	agentTests: (
		agent_id: string,
		query?: operations["list_agent_tests_route_api_agent_evaluations_agents__agent_id__tests_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET("/api/agent-evaluations/agents/{agent_id}/tests", {
				params: { path: { agent_id }, query },
			}),
		),
	agentTest: (agent_id: string, logical_test_id: string) =>
		result(
			apiClient.GET(
				"/api/agent-evaluations/agents/{agent_id}/tests/{logical_test_id}",
				{ params: { path: { agent_id, logical_test_id } } },
			),
		),
	createAgentTest: (agent_id: string, body: Schema["AgentTestCreate"]) =>
		result(
			apiClient.POST("/api/agent-evaluations/agents/{agent_id}/tests", {
				params: { path: { agent_id } },
				body,
			}),
		),
	editAgentTest: (
		agent_id: string,
		logical_test_id: string,
		body: Schema["AgentTestUpdate"],
	) =>
		result(
			apiClient.PATCH(
				"/api/agent-evaluations/agents/{agent_id}/tests/{logical_test_id}",
				{ params: { path: { agent_id, logical_test_id } }, body },
			),
		),
	latestAgentTests: (
		agent_id: string,
		query?: operations["list_agent_tests_latest_route_api_agent_evaluations_agents__agent_id__tests_latest_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET(
				"/api/agent-evaluations/agents/{agent_id}/tests/latest",
				{ params: { path: { agent_id }, query } },
			),
		),
	runAgentTests: (agent_id: string, body: Schema["AgentTestsRunCreate"]) =>
		admitted(
			apiClient.POST(
				"/api/agent-evaluations/agents/{agent_id}/tests/run",
				{ params: { path: { agent_id } }, body },
			),
			"X-Evaluation-Execution-Id",
			"executionId",
			"Test execution was queued, but its link was missing. Check Notifications before starting another execution.",
		),
	recordedEvaluation: (body: Schema["RecordedEvaluationCreate"]) =>
		admitted(
			apiClient.POST("/api/agent-evaluations/recorded-evaluations", {
				body,
			}),
			"X-Recorded-Evaluation-Id",
			"evaluationId",
			"Recorded evaluation was queued, but its link was missing. Check Notifications before starting another evaluation.",
		),
	recordedEvaluationResults: (
		evaluation_id: string,
		query?: operations["get_recorded_evaluation_results_api_agent_evaluations_recorded_evaluations__evaluation_id__results_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET(
				"/api/agent-evaluations/recorded-evaluations/{evaluation_id}/results",
				{ params: { path: { evaluation_id }, query } },
			),
		),
	recordedEvaluationUsage: (
		evaluation_id: string,
		query?: operations["get_recorded_evaluation_usage_api_agent_evaluations_recorded_evaluations__evaluation_id__usage_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET(
				"/api/agent-evaluations/recorded-evaluations/{evaluation_id}/usage",
				{ params: { path: { evaluation_id }, query } },
			),
		),
	reviews: (
		query: operations["list_reviews_api_agent_reviews_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET("/api/agent-reviews", {
				params: { query },
			}),
		),
	review: (review_id: string) =>
		result(
			apiClient.GET("/api/agent-reviews/{review_id}", {
				params: { path: { review_id } },
			}),
		),
	reviewRun: (review_run_id: string) =>
		result(
			apiClient.GET("/api/agent-reviews/runs/{review_run_id}", {
				params: { path: { review_run_id } },
			}),
		),
	reviewResults: (review_run_id: string) =>
		result(
			apiClient.GET("/api/agent-reviews/runs/{review_run_id}/results", {
				params: { path: { review_run_id } },
			}),
		),
	reviewUsage: (
		review_run_id: string,
		query?: operations["get_usage_api_agent_reviews_runs__review_run_id__usage_get"]["parameters"]["query"],
	) =>
		result(
			apiClient.GET("/api/agent-reviews/runs/{review_run_id}/usage", {
				params: { path: { review_run_id }, query },
			}),
		),
	createReviewRun: (review_id: string, body: Schema["AgentReviewRunCreate"]) =>
		admitted(
			apiClient.POST("/api/agent-reviews/{review_id}/runs", {
				params: { path: { review_id } },
				body,
			}),
			"X-Agent-Review-Run-Id",
			"reviewRunId",
			"Review run was queued, but its link was missing. Check Notifications before starting another review.",
		),
};
