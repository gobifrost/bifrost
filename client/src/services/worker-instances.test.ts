import { $api } from "@/lib/api-client";
import { useWorkerInstances } from "./worker-instances";

vi.mock("@/lib/api-client", () => ({ $api: { useQuery: vi.fn() } }));

it("passes the filter and cursor to the worker inventory API", () => {
	const query = {
		form: "external" as const,
		before_created_at: "2026-11-27T00:00:00Z",
		before_id: "47fb9102-3944-4139-baa6-3f90556bb334",
	};

	useWorkerInstances(query);

	expect($api.useQuery).toHaveBeenCalledWith(
		"get",
		"/api/platform/workers/instances",
		{ params: { query } },
	);
});
