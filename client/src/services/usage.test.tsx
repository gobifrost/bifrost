import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

const useQuery = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api-client", () => ({
	$api: { useQuery },
}));

import { useUsageReport } from "./usage";

describe("useUsageReport", () => {
	it("passes enabled=false to suppress hidden or invalid report queries", () => {
		useQuery.mockReturnValue({ data: null });

		renderHook(() =>
			useUsageReport("2026-08-01", "2026-08-20", "all", "org-1", {
				enabled: false,
			}),
		);

		expect(useQuery).toHaveBeenCalledWith(
			"get",
			"/api/reports/usage",
			{
				params: {
					query: {
						start_date: "2026-08-01",
						end_date: "2026-08-20",
						source: "all",
						org_id: "org-1",
					},
				},
			},
			{ enabled: false },
		);
	});
});
