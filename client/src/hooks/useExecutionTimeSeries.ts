import { useMemo } from "react";
import { $api } from "@/lib/api-client";
import type { ChartWindow } from "@/lib/execution-buckets";

function browserTimeZone(): string {
	return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** Fetch the volume-independent aggregate for a dashboard chart window. */
export function useExecutionTimeSeries(window: ChartWindow) {
	const timezone = useMemo(() => browserTimeZone(), []);
	return $api.useQuery(
		"get",
		"/api/metrics/executions/timeseries",
		{
			params: { query: { window, timezone } },
		},
		{
			staleTime: 60000,
		},
	);
}
