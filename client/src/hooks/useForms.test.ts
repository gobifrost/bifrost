import { expect, it, vi } from "vitest";
const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ apiClient: { POST: post }, $api: {} }));
import { executeFormStartup } from "./useForms";
it("loads startup with the selected organization", async () => {
	post.mockResolvedValue({
		data: { result: { customer: "Contoso" }, startup_handle: "handle" },
	});
	await executeFormStartup("form-1", { ticket: "123" }, "contoso");
	expect(post).toHaveBeenCalledWith("/api/forms/{form_id}/startup", {
		params: { path: { form_id: "form-1" }, query: { org_id: "contoso" } },
		body: { ticket: "123" },
	});
});
