import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api-client";
import { agentPlatform } from "./agentPlatform";
vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() },
}));
beforeEach(() => {
	vi.resetAllMocks();
	for (const method of [apiClient.GET, apiClient.POST, apiClient.PUT])
		vi.mocked(method).mockResolvedValue({
			data: { id: "saved" },
			response: new Response(null, {
				headers: { "X-Evaluation-Execution-Id": "execution" },
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
});

it("loads subsequent suite pages through the existing offset contract", async () => {
	await agentPlatform.suites(50);
	expect(apiClient.GET).toHaveBeenCalledWith(
		"/api/agent-evaluations/suites",
		{ params: { query: { offset: 50, limit: 50 } } },
	);
});
