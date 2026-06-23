import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: vi.fn(),
		POST: vi.fn(),
		PUT: vi.fn(),
		DELETE: vi.fn(),
	},
}));
import { apiClient } from "@/lib/api-client";
import {
	listPolicyRules,
	policyRuleUsages,
	createPolicyRule,
	updatePolicyRule,
	deletePolicyRule,
} from "./policyRules";

const mockGet = (apiClient.GET as unknown) as ReturnType<typeof vi.fn>;
const mockPost = (apiClient.POST as unknown) as ReturnType<typeof vi.fn>;
const mockPut = (apiClient.PUT as unknown) as ReturnType<typeof vi.fn>;
const mockDeleteFn = (apiClient.DELETE as unknown) as ReturnType<typeof vi.fn>;

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
	beforeEach(() => {
		mockGet.mockReset();
		mockPost.mockReset();
		mockPut.mockReset();
		mockDeleteFn.mockReset();
	});

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

// ---------------------------------------------------------------------------
// createPolicyRule
// ---------------------------------------------------------------------------
describe("createPolicyRule", () => {
	beforeEach(() => mockPost.mockReset());

	it("calls POST /api/policy-rules with body and returns the new rule", async () => {
		mockPost.mockResolvedValue({ data: RULE, error: undefined });
		const body = { name: "admin_bypass", domain: "file" as const, body: { policies: [] } };
		const result = await createPolicyRule(body);
		expect(mockPost).toHaveBeenCalledWith("/api/policy-rules", { body });
		expect(result).toEqual(RULE);
	});

	it("throws on API error", async () => {
		mockPost.mockResolvedValue({ data: undefined, error: { detail: "Conflict" } });
		await expect(createPolicyRule({ name: "x", domain: "file", body: {} })).rejects.toThrow("Conflict");
	});

	it("throws when data is undefined", async () => {
		mockPost.mockResolvedValue({ data: undefined, error: undefined });
		await expect(createPolicyRule({ name: "x", domain: "file", body: {} })).rejects.toThrow(
			"No data returned",
		);
	});
});

// ---------------------------------------------------------------------------
// updatePolicyRule
// ---------------------------------------------------------------------------
describe("updatePolicyRule", () => {
	beforeEach(() => mockPut.mockReset());

	it("calls PUT /api/policy-rules/{domain}/{name} with body", async () => {
		mockPut.mockResolvedValue({ data: RULE, error: undefined });
		const body = { description: "Updated" };
		const result = await updatePolicyRule("file", "admin_bypass", body);
		expect(mockPut).toHaveBeenCalledWith(
			"/api/policy-rules/{domain}/{name}",
			{ params: { path: { domain: "file", name: "admin_bypass" } }, body },
		);
		expect(result).toEqual(RULE);
	});

	it("throws on API error", async () => {
		mockPut.mockResolvedValue({ data: undefined, error: { detail: "Not found" } });
		await expect(updatePolicyRule("file", "missing", {})).rejects.toThrow("Not found");
	});
});

// ---------------------------------------------------------------------------
// deletePolicyRule
// ---------------------------------------------------------------------------
describe("deletePolicyRule", () => {
	beforeEach(() => mockDeleteFn.mockReset());

	it("calls DELETE /api/policy-rules/{domain}/{name} and resolves on 204", async () => {
		mockDeleteFn.mockResolvedValue({ error: undefined });
		await expect(deletePolicyRule("file", "admin_bypass")).resolves.toBeUndefined();
		expect(mockDeleteFn).toHaveBeenCalledWith(
			"/api/policy-rules/{domain}/{name}",
			{ params: { path: { domain: "file", name: "admin_bypass" } } },
		);
	});

	it("throws a plain error on a non-409 API error", async () => {
		mockDeleteFn.mockResolvedValue({ error: { detail: "Forbidden" } });
		await expect(deletePolicyRule("file", "x")).rejects.toThrow("Forbidden");
	});

	it("throws with cause.type='in_use' when server returns a usages payload", async () => {
		const usages = {
			file_policies: [{ id: "fp-1", location: "workspace", path: "rep/", organization_id: null }],
			tables: [],
			total: 1,
		};
		mockDeleteFn.mockResolvedValue({
			error: {
				detail: {
					message: "Rule 'x' is in use",
					usages,
				},
			},
		});
		try {
			await deletePolicyRule("file", "x");
			throw new Error("should have thrown");
		} catch (err) {
			expect(err).toBeInstanceOf(Error);
			const e = err as Error & { cause?: { type?: string; usages?: unknown } };
			expect(e.cause?.type).toBe("in_use");
			expect(e.cause?.usages).toEqual(usages);
		}
	});
});
