import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api-client", () => ({ apiClient: { GET: vi.fn() } }));
import { apiClient } from "@/lib/api-client";
import { listPolicyRules, policyRuleUsages } from "./policyRules";

const mockGet = (apiClient.GET as unknown) as ReturnType<typeof vi.fn>;

const RULE = {
	id: "00000000-0000-0000-0000-000000000001",
	organization_id: null,
	name: "admin_bypass",
	domain: "file" as const,
	description: "Platform admins bypass all file policies",
	body: { policies: [] },
	read_only: true,
	created_at: "2024-01-01T00:00:00Z",
	updated_at: "2024-01-01T00:00:00Z",
};

describe("listPolicyRules", () => {
	beforeEach(() => mockGet.mockReset());

	it("calls GET /api/policy-rules with no query when domain is omitted", async () => {
		mockGet.mockResolvedValue({ data: [RULE], error: undefined });
		const result = await listPolicyRules();
		expect(mockGet).toHaveBeenCalledWith("/api/policy-rules", {
			params: { query: undefined },
		});
		expect(result).toEqual([RULE]);
	});

	it("passes domain filter when provided", async () => {
		mockGet.mockResolvedValue({ data: [RULE], error: undefined });
		await listPolicyRules("file");
		expect(mockGet).toHaveBeenCalledWith("/api/policy-rules", {
			params: { query: { domain: "file" } },
		});
	});

	it("filters by table domain", async () => {
		mockGet.mockResolvedValue({ data: [], error: undefined });
		const result = await listPolicyRules("table");
		expect(mockGet).toHaveBeenCalledWith("/api/policy-rules", {
			params: { query: { domain: "table" } },
		});
		expect(result).toEqual([]);
	});

	it("throws on API error", async () => {
		mockGet.mockResolvedValue({ data: undefined, error: { detail: "Unauthorized" } });
		await expect(listPolicyRules()).rejects.toThrow("Unauthorized");
	});

	it("returns empty array when data is nullish", async () => {
		mockGet.mockResolvedValue({ data: null, error: undefined });
		const result = await listPolicyRules();
		expect(result).toEqual([]);
	});
});

describe("policyRuleUsages", () => {
	beforeEach(() => mockGet.mockReset());

	const USAGES = {
		file_policies: [{ location: "workspace", path: "reports/", scope: null }],
		tables: [],
		total: 1,
	};

	it("calls GET /api/policy-rules/{domain}/{name}/usages", async () => {
		mockGet.mockResolvedValue({ data: USAGES, error: undefined });
		const result = await policyRuleUsages("file", "admin_bypass");
		expect(mockGet).toHaveBeenCalledWith(
			"/api/policy-rules/{domain}/{name}/usages",
			{ params: { path: { domain: "file", name: "admin_bypass" } } },
		);
		expect(result).toEqual(USAGES);
	});

	it("throws on API error", async () => {
		mockGet.mockResolvedValue({ data: undefined, error: { detail: "Not found" } });
		await expect(policyRuleUsages("file", "missing")).rejects.toThrow("Not found");
	});
});
