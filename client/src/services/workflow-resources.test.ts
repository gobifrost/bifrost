import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useWorkflowResourceReport } from "./workflow-resources";

const mockUseQuery = vi.fn();
vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: (...args: unknown[]) => mockUseQuery(...args) },
}));

describe("useWorkflowResourceReport", () => {
	it("passes filters and pagination to the resource report endpoint", () => {
		renderHook(() =>
			useWorkflowResourceReport({
				startedAfter: "2026-09-24T00:00:00Z",
				startedBefore: "2026-09-25T00:00:00Z",
				view: "runs",
				sort: "cpu",
				page: 2,
				pageSize: 50,
				orgId: "org-1",
				workflowId: "workflow-1",
				workflow: "patch",
				status: "Failed",
			}),
		);
		expect(mockUseQuery).toHaveBeenCalledWith(
			"get",
			"/api/reports/workflow-resources",
			{
				params: {
					query: {
						started_after: "2026-09-24T00:00:00Z",
						started_before: "2026-09-25T00:00:00Z",
						view: "runs",
						sort: "cpu",
						page: 2,
						page_size: 50,
						org_id: "org-1",
						workflow_id: "workflow-1",
						workflow: "patch",
						status: "Failed",
					},
				},
			},
		);
	});
});
