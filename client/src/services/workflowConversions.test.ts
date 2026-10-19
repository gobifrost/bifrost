import { describe, expect, it, vi } from "vitest";

import {
	applyWorkflowConversion,
	analyzeWorkflowConversions,
	getWorkflowConversionReport,
} from "./workflowConversions";

const get = vi.fn();
const post = vi.fn();

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: (...args: unknown[]) => get(...args),
		POST: (...args: unknown[]) => post(...args),
	},
}));

describe("workflow conversion service", () => {
	it("starts a frozen selected workflow analysis", async () => {
		post.mockResolvedValueOnce({
			data: { job_id: "job-synthetic" },
			error: undefined,
		});

		await expect(
			analyzeWorkflowConversions({
				workflow_ids: ["workflow-synthetic"],
				platform_cutover: false,
			}),
		).resolves.toEqual({ job_id: "job-synthetic" });
		expect(post).toHaveBeenCalledWith("/api/workflow-conversions", {
			body: {
				workflow_ids: ["workflow-synthetic"],
				platform_cutover: false,
			},
		});
	});

	it("reads a bounded report page and applies only reviewed proposals", async () => {
		get.mockResolvedValueOnce({
			data: { job_id: "job-synthetic", items: [] },
			error: undefined,
		});
		post.mockResolvedValueOnce({
			data: { job_id: "apply-synthetic" },
			error: undefined,
		});

		await getWorkflowConversionReport("job-synthetic", {
			offset: 25,
			limit: 25,
		});
		await applyWorkflowConversion("job-synthetic", {
			selections: [
				{
					workflow_id: "workflow-synthetic",
					source_fingerprint: "fingerprint",
					acknowledged_unknowns: true,
				},
			],
			acknowledged_solution_requests: true,
		});

		expect(get).toHaveBeenCalledWith(
			"/api/workflow-conversions/{job_id}/report",
			{
				params: {
					path: { job_id: "job-synthetic" },
					query: { offset: 25, limit: 25 },
				},
			},
		);
		expect(post).toHaveBeenCalledWith(
			"/api/workflow-conversions/{job_id}/apply",
			expect.objectContaining({
				params: { path: { job_id: "job-synthetic" } },
			}),
		);
	});
});
