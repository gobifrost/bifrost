import { describe, expect, it, vi } from "vitest";
const useQuery = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ $api: { useQuery } }));
import { useWorkflowParameters } from "./workflowParameters";

describe("useWorkflowParameters", () => {
	it("loads only the selected workflow’s execution parameters", () => {
		useWorkflowParameters("workflow-id");
		expect(useQuery).toHaveBeenLastCalledWith(
			"get",
			"/api/workflows/{workflow_id}/parameters",
			{ params: { path: { workflow_id: "workflow-id" } } },
			{ enabled: true },
		);
	});
	it("does not request parameters for a closed dialog or deleted workflow", () => {
		useWorkflowParameters(null);
		expect(useQuery).toHaveBeenLastCalledWith(
			"get",
			"/api/workflows/{workflow_id}/parameters",
			{ params: { path: { workflow_id: null } } },
			{ enabled: false },
		);
	});
});
