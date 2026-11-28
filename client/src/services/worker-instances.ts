import { $api } from "@/lib/api-client";
import type { components, paths } from "@/lib/v1";

export type WorkerInstanceCursor =
	components["schemas"]["WorkerInstanceCursor"];
export type WorkerInstanceQuery = NonNullable<
	paths["/api/platform/workers/instances"]["get"]["parameters"]["query"]
>;

export function useWorkerInstances(query: WorkerInstanceQuery) {
	return $api.useQuery("get", "/api/platform/workers/instances", {
		params: { query },
	});
}
