import { useMemo } from "react";
import { $api } from "@/lib/api-client";
import { windowStartIso, type ChartWindow } from "@/lib/execution-buckets";

/**
 * Fetch the executions covering a dashboard chart window. Bucketing
 * happens client-side via `bucketExecutions`.
 */
export function useExecutionsWindow(window: ChartWindow) {
	const startDate = useMemo(() => windowStartIso(window), [window]);
	// NOTE: never override queryKey on $api.useQuery — openapi-react-query
	// derives the request from the key ([method, path, init]), so a custom
	// key silently breaks the fetch. The default key already includes the
	// params, so each window is cached separately.
	return $api.useQuery(
		"get",
		"/api/executions",
		{
			params: { query: { startDate, limit: 1000 } },
		},
		{
			staleTime: 60000,
		},
	);
}
