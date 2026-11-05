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

it("preserves Global scope instead of using the caller's default organization", async () => {
	post.mockResolvedValue({ data: { execution_id: "global-run" } });
	await executeWorkflowWithContext(
		"workflow",
		{},
		undefined,
		undefined,
		undefined,
		{ orgId: null },
	);
	expect(post).toHaveBeenLastCalledWith("/api/workflows/execute", {
		body: expect.objectContaining({ org_id: "global" }),
	});
});
