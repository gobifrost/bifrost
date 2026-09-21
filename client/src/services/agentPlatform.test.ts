import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api-client";
import { agentPlatform } from "./agentPlatform";
vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() },
}));
beforeEach(() => {
	vi.resetAllMocks();
	for (const method of [
		apiClient.GET,
		apiClient.POST,
		apiClient.PUT,
		apiClient.PATCH,
	])
		vi.mocked(method).mockResolvedValue({
			data: { id: "saved" },
			response: new Response(null, {
				headers: {
					"X-Evaluation-Execution-Id": "execution",
					"X-Recorded-Evaluation-Id": "evaluation",
					"X-Agent-Review-Run-Id": "review-run",
				},
			}),
		} as never);
});
describe("Agent Platform API", () => {
	const reads: [string, () => Promise<unknown>, string][] = [
		[
			"snapshot",
			() => agentPlatform.snapshot("run"),
			"/api/agent-runs/{run_id}/snapshot",
		],
		[
			"tree",
			() => agentPlatform.tree("run"),
			"/api/agent-runs/{run_id}/tree",
		],
		[
			"timeline",
			() =>
				agentPlatform.timeline("run", {
					cursor: "opaque",
					kind: "tool",
					attempt: 2,
				}),
			"/api/agent-runs/{run_id}/timeline",
		],
		[
			"checkpoints",
			() => agentPlatform.checkpoints("run", "opaque"),
			"/api/agent-runs/{run_id}/checkpoints",
		],
		[
			"suites",
			() => agentPlatform.suites(),
			"/api/agent-evaluations/suites",
		],
		[
			"suite",
			() => agentPlatform.suite("suite"),
			"/api/agent-evaluations/suites/{suite_id}",
		],
		[
			"cases",
			() => agentPlatform.cases("suite"),
			"/api/agent-evaluations/suites/{suite_id}/cases",
		],
		[
			"candidate",
			() => agentPlatform.candidate("candidate"),
			"/api/agent-evaluations/candidates/{candidate_id}",
		],
		[
			"execution",
			() => agentPlatform.execution("execution"),
			"/api/agent-evaluations/executions/{execution_id}",
		],
		[
			"results",
			() => agentPlatform.results("execution"),
			"/api/agent-evaluations/executions/{execution_id}/results",
		],
		[
			"execution usage",
			() => agentPlatform.executionUsage("execution", { limit: 10 }),
			"/api/agent-evaluations/executions/{execution_id}/usage",
		],
		[
			"findings",
			() => agentPlatform.findings("agent"),
			"/api/agent-findings",
		],
		[
			"finding search",
			() => agentPlatform.searchFindings({ q: "latency", limit: 10 }),
			"/api/agent-findings/search",
		],
		[
			"matrix",
			() => agentPlatform.matrix("matrix"),
			"/api/agent-evaluations/executions/batch/{matrix_id}",
		],
		[
			"agent tests",
			() => agentPlatform.agentTests("agent", { offset: 20, limit: 10 }),
			"/api/agent-evaluations/agents/{agent_id}/tests",
		],
		[
			"agent test",
			() => agentPlatform.agentTest("agent", "logical"),
			"/api/agent-evaluations/agents/{agent_id}/tests/{logical_test_id}",
		],
		[
			"latest agent tests",
			() =>
				agentPlatform.latestAgentTests("agent", { offset: 20, limit: 10 }),
			"/api/agent-evaluations/agents/{agent_id}/tests/latest",
		],
		[
			"recorded evaluation results",
			() =>
				agentPlatform.recordedEvaluationResults("evaluation", {
					offset: 20,
					limit: 10,
				}),
			"/api/agent-evaluations/recorded-evaluations/{evaluation_id}/results",
		],
		[
			"recorded evaluation usage",
			() =>
				agentPlatform.recordedEvaluationUsage("evaluation", {
					offset: 20,
					limit: 10,
				}),
			"/api/agent-evaluations/recorded-evaluations/{evaluation_id}/usage",
		],
		[
			"reviews",
			() => agentPlatform.reviews({ agent_id: "agent", status: "active" }),
			"/api/agent-reviews",
		],
		[
			"review",
			() => agentPlatform.review("review"),
			"/api/agent-reviews/{review_id}",
		],
		[
			"review run",
			() => agentPlatform.reviewRun("review-run"),
			"/api/agent-reviews/runs/{review_run_id}",
		],
		[
			"review results",
			() => agentPlatform.reviewResults("review-run"),
			"/api/agent-reviews/runs/{review_run_id}/results",
		],
		[
			"review usage",
			() => agentPlatform.reviewUsage("review-run", { limit: 10 }),
			"/api/agent-reviews/runs/{review_run_id}/usage",
		],
	];
	it.each(reads)(
		"reads %s through the typed API",
		async (_name, read, path) => {
			expect(await read()).toEqual({ id: "saved" });
			expect(vi.mocked(apiClient.GET).mock.calls[0][0]).toBe(path);
		},
	);
	it("preserves cursor and timeline filters", async () => {
		await agentPlatform.timeline("run", {
			cursor: "opaque",
			kind: "tool",
			attempt: 2,
		});
		expect(apiClient.GET).toHaveBeenCalledWith(
			"/api/agent-runs/{run_id}/timeline",
			{
				params: {
					path: { run_id: "run" },
					query: { cursor: "opaque", kind: "tool", attempt: 2 },
				},
			},
		);
	});
	it("keeps all mutations explicit and transports concurrency versions", async () => {
		await agentPlatform.createSuite({ name: "suite" });
		await agentPlatform.updateSuite("suite", {
			expected_version: 4,
			name: "changed",
		});
		await agentPlatform.publish("suite");
		await agentPlatform.createCase("suite", {
			name: "case",
			enabled: true,
			position: 0,
			repetitions: 1,
			provenance: "manual",
		});
		await agentPlatform.updateCase("suite", "case", {
			expected_version: 2,
		});
		await agentPlatform.accept("suite", "draft");
		await agentPlatform.designer("suite", {
			suite_goal: "test",
			requested_count: 2,
			historical_run_ids: ["run"],
		});
		await agentPlatform.createCandidate({
			base_agent_id: "agent",
			overlays: { system_prompt: "candidate" },
		});
		await agentPlatform.cancel("execution");
		expect(apiClient.POST).toHaveBeenCalledTimes(7);
		expect(apiClient.PUT).toHaveBeenCalledWith(
			"/api/agent-evaluations/suites/{suite_id}/cases/{case_id}",
			{
				params: { path: { suite_id: "suite", case_id: "case" } },
				body: { expected_version: 2 },
			},
		);
	});
	it("reads execution identity from the accepted response header", async () => {
		expect(await agentPlatform.execute({ suite_id: "suite" })).toEqual({
			id: "saved",
			executionId: "execution",
		});
	});
	it("reads recorded evaluation identity from the accepted response header", async () => {
		expect(
			await agentPlatform.recordedEvaluation({
				agent_id: "agent",
				run_ids: ["run"],
				all_tests: false,
				applicability: "unknown",
				judge_mode: "exact",
			}),
		).toEqual({
			id: "saved",
			evaluationId: "evaluation",
		});
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/agent-evaluations/recorded-evaluations",
			{
				body: {
					agent_id: "agent",
					run_ids: ["run"],
					all_tests: false,
					applicability: "unknown",
					judge_mode: "exact",
				},
			},
		);
	});
	it("reads agent test run identity from the accepted response header", async () => {
		expect(
			await agentPlatform.runAgentTests("agent", {
				selections: [{ case_id: "case", case_version: 3 }],
			}),
		).toEqual({
			id: "saved",
			executionId: "execution",
		});
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/agent-evaluations/agents/{agent_id}/tests/run",
			{
				params: { path: { agent_id: "agent" } },
				body: { selections: [{ case_id: "case", case_version: 3 }] },
			},
		);
	});
	it("reads review run identity from the accepted response header", async () => {
		expect(
			await agentPlatform.createReviewRun("review", {
				run_ids: ["run"],
			}),
		).toEqual({
			id: "saved",
			reviewRunId: "review-run",
		});
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/agent-reviews/{review_id}/runs",
			{
				params: { path: { review_id: "review" } },
				body: { run_ids: ["run"] },
			},
		);
	});
	it("reports missing admission identity headers for each queued helper", async () => {
		vi.mocked(apiClient.POST).mockResolvedValue({
			data: { job_id: "job" },
			response: new Response(),
		} as never);
		await expect(
			agentPlatform.runAgentTests("agent", { selections: [] }),
		).rejects.toThrow("Test execution was queued");
		await expect(
			agentPlatform.recordedEvaluation({
				agent_id: "agent",
				run_ids: ["run"],
				all_tests: false,
				applicability: "unknown",
				judge_mode: "exact",
			}),
		).rejects.toThrow("Recorded evaluation was queued");
		await expect(
			agentPlatform.createReviewRun("review", { run_ids: ["run"] }),
		).rejects.toThrow("Review run was queued");
		expect(apiClient.POST).toHaveBeenCalledTimes(3);
	});
	it("reports enqueue ambiguity without resubmitting", async () => {
		vi.mocked(apiClient.POST).mockResolvedValue({
			data: { job_id: "job" },
			response: new Response(),
		} as never);
		await expect(
			agentPlatform.execute({ suite_id: "suite" }),
		).rejects.toThrow("Execution was queued");
		expect(apiClient.POST).toHaveBeenCalledOnce();
	});
	it("propagates authorization errors", async () => {
		vi.mocked(apiClient.GET).mockResolvedValue({
			error: new Error("Not found"),
		} as never);
		await expect(agentPlatform.snapshot("run")).rejects.toThrow(
			"Not found",
		);
	});
	it("creates and dismisses findings without tests", async () => {
		await agentPlatform.createFinding({
			agent_id: "agent",
			description: "Wrong tool call.",
			source_kind: "manual",
			finding_kind: "problem",
		});
		expect(apiClient.POST).toHaveBeenCalledWith("/api/agent-findings", {
			body: {
				agent_id: "agent",
				description: "Wrong tool call.",
				source_kind: "manual",
				finding_kind: "problem",
			},
		});
		await agentPlatform.updateFinding("finding", {
			status: "dismissed",
		});
		expect(apiClient.PATCH).toHaveBeenCalledWith(
			"/api/agent-findings/{finding_id}",
			{
				params: { path: { finding_id: "finding" } },
				body: { status: "dismissed" },
			},
		);
	});
	it("admits and cancels saved matrices through the shared contract", async () => {
		await agentPlatform.executeBatch({
			suite_id: "suite",
			candidate_ids: ["candidate"],
			profile_ids: ["profile"],
		});
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/agent-evaluations/executions/batch",
			{
				body: {
					suite_id: "suite",
					candidate_ids: ["candidate"],
					profile_ids: ["profile"],
				},
			},
		);
		await agentPlatform.cancelMatrix("matrix");
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/agent-evaluations/executions/batch/{matrix_id}/cancel",
			{ params: { path: { matrix_id: "matrix" } } },
		);
	});
	it("manages agent-wide tests through generated routes", async () => {
		await agentPlatform.createAgentTest("agent", {
			name: "Regression",
			position: 0,
			enabled: true,
			input: { prompt: "What happened?" },
			assertions: [{ kind: "contains", value: "safely" }],
			repetitions: 1,
			provenance: "manual",
		});
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/agent-evaluations/agents/{agent_id}/tests",
			{
				params: { path: { agent_id: "agent" } },
				body: {
					name: "Regression",
					position: 0,
					enabled: true,
					input: { prompt: "What happened?" },
					assertions: [{ kind: "contains", value: "safely" }],
					repetitions: 1,
					provenance: "manual",
				},
			},
		);
		await agentPlatform.editAgentTest("agent", "logical", {
			enabled: false,
			expected_version: 3,
		});
		expect(apiClient.PATCH).toHaveBeenCalledWith(
			"/api/agent-evaluations/agents/{agent_id}/tests/{logical_test_id}",
			{
				params: {
					path: { agent_id: "agent", logical_test_id: "logical" },
				},
				body: { enabled: false, expected_version: 3 },
			},
		);
		await agentPlatform.runAgentTests("agent", {
			selections: [{ case_id: "case", case_version: 3 }],
		});
		expect(apiClient.POST).toHaveBeenLastCalledWith(
			"/api/agent-evaluations/agents/{agent_id}/tests/run",
			{
				params: { path: { agent_id: "agent" } },
				body: { selections: [{ case_id: "case", case_version: 3 }] },
			},
		);
	});
});

it("loads subsequent suite pages through the existing offset contract", async () => {
	await agentPlatform.suites(50);
	expect(apiClient.GET).toHaveBeenCalledWith(
		"/api/agent-evaluations/suites",
		{ params: { query: { offset: 50, limit: 50 } } },
	);
});
