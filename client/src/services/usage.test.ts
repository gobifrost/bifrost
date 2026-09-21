import { beforeEach, describe, expect, it, vi } from "vitest";
import { $api } from "@/lib/api-client";
import { useUsageBreakdown, useUsageReport } from "./usage";

vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: vi.fn() },
}));

beforeEach(() => {
	vi.resetAllMocks();
});

describe("usage service", () => {
	it.each([
		["all organizations", undefined, {}],
		["global rows only", null, { global_only: true }],
		["one organization", "org-1", { org_id: "org-1" }],
	] as const)(
		"fetches the main usage report for %s",
		(_label, orgId, expectedScopeParams) => {
			useUsageReport("2026-09-01", "2026-09-21", "all", orgId);

			expect($api.useQuery).toHaveBeenCalledWith(
				"get",
				"/api/reports/usage",
				{
					params: {
						query: {
							start_date: "2026-09-01",
							end_date: "2026-09-21",
							source: "all",
							...expectedScopeParams,
						},
					},
				},
				{ enabled: true },
			);
		},
	);

	it("fetches usage breakdown with the generated route and filters", () => {
		useUsageBreakdown({
			start_date: "2026-09-01",
			end_date: "2026-09-21",
			org_id: "org",
			source: "agents",
			purpose: "agent_review",
			provider: "openai",
			model: "gpt-5",
			profile_id: "profile",
			profile_fingerprint: "fingerprint",
			limit: 25,
			offset: 50,
		});

		expect($api.useQuery).toHaveBeenCalledWith(
			"get",
			"/api/reports/usage/breakdown",
			{
				params: {
					query: {
						start_date: "2026-09-01",
						end_date: "2026-09-21",
						org_id: "org",
						source: "agents",
						purpose: "agent_review",
						provider: "openai",
						model: "gpt-5",
						profile_id: "profile",
						profile_fingerprint: "fingerprint",
						limit: 25,
						offset: 50,
					},
				},
			},
			{ enabled: true },
		);
	});

	it("does not enable the breakdown query until dates are present", () => {
		useUsageBreakdown({
			start_date: "",
			end_date: "2026-09-21",
		});

		expect($api.useQuery).toHaveBeenCalledWith(
			"get",
			"/api/reports/usage/breakdown",
			{
				params: {
					query: {
						start_date: "",
						end_date: "2026-09-21",
					},
				},
			},
			{ enabled: false },
		);
	});

	it.each([
		["all organizations", undefined, {}],
		["global rows only", null, { global_only: true }],
		["one organization", "org-1", { org_id: "org-1" }],
	] as const)(
		"normalizes breakdown organization scope for %s",
		(_label, orgId, expectedScopeParams) => {
			useUsageBreakdown({
				start_date: "2026-09-01",
				end_date: "2026-09-21",
				source: "all",
				org_id: typeof orgId === "string" ? orgId : undefined,
				global_only: orgId === null ? true : undefined,
			});

			expect($api.useQuery).toHaveBeenCalledWith(
				"get",
				"/api/reports/usage/breakdown",
				{
					params: {
						query: {
							start_date: "2026-09-01",
							end_date: "2026-09-21",
							source: "all",
							...expectedScopeParams,
						},
					},
				},
				{ enabled: true },
			);
		},
	);
});
