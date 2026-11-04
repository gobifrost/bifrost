import { expect, it, vi } from "vitest";
const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ apiClient: { POST: post }, $api: {} }));
import { executeWorkflowWithContext } from "./useWorkflows";
it("preserves the execution organization when rerunning", async () => {
	post.mockResolvedValue({ data: { execution_id: "new-run" } });
	await executeWorkflowWithContext(
		"workflow",
		{ summary: "test" },
		undefined,
		undefined,
		undefined,
		{ orgId: "contoso" },
	);
	expect(post).toHaveBeenCalledWith("/api/workflows/execute", {
		body: expect.objectContaining({
			workflow_id: "workflow",
			org_id: "contoso",
			input_data: { summary: "test" },
		}),
	});
});
