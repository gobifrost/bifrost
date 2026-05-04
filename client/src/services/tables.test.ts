/**
 * Sibling tests for the imperative tables service wrappers.
 *
 * Scope: just `validatePolicies` for now. The hooks (`useTables`,
 * `useDocuments`, …) are exercised through their consumer components.
 * The other imperative wrappers in this file pre-date the per-service
 * test rule and are out of scope for this commit.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// `apiClient` is the openapi-fetch instance; mock the POST surface so we
// can assert the route + body without standing up MSW.
const mockPost = vi.fn();
vi.mock("@/lib/api-client", () => ({
	apiClient: { POST: (...args: unknown[]) => mockPost(...args) },
	$api: {},
}));

// Sonner is touched by other exports in tables.ts at import time.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { validatePolicies } from "./tables";

beforeEach(() => {
	mockPost.mockReset();
});

describe("validatePolicies", () => {
	it("posts to /api/tables/policies/validate with the body", async () => {
		mockPost.mockResolvedValue({ data: { ok: true, errors: [] } });
		const body = { policies: [] };
		const out = await validatePolicies(body);
		expect(mockPost).toHaveBeenCalledTimes(1);
		const [path, opts] = mockPost.mock.calls[0]!;
		expect(path).toBe("/api/tables/policies/validate");
		expect(opts.body).toEqual(body);
		expect(out).toEqual({ ok: true, errors: [] });
	});

	it("returns the structured error response on validation failure", async () => {
		// The server still returns 200 on validation failure (errors are in
		// the body). The wrapper must NOT throw — it should pass the
		// structured response through to the caller.
		mockPost.mockResolvedValue({
			data: {
				ok: false,
				errors: [{ path: "$.policies[0]", message: "broken" }],
			},
		});
		const out = await validatePolicies({ policies: [{ broken: true }] });
		expect(out.ok).toBe(false);
		expect(out.errors).toEqual([
			{ path: "$.policies[0]", message: "broken" },
		]);
	});

	it("forwards an AbortSignal to apiClient", async () => {
		mockPost.mockResolvedValue({ data: { ok: true, errors: [] } });
		const controller = new AbortController();
		await validatePolicies({ policies: [] }, { signal: controller.signal });
		const [, opts] = mockPost.mock.calls[0]!;
		expect(opts.signal).toBe(controller.signal);
	});

	it("throws on transport / 5xx errors so the editor can fall back", async () => {
		// Transport-layer failure is distinct from a validation 200 with
		// `ok: false`. The wrapper should surface the transport error so
		// the editor's catch path can clear stale validation results
		// without rendering nonsense.
		mockPost.mockResolvedValue({
			data: undefined,
			error: { detail: "boom" },
		});
		await expect(validatePolicies({ policies: [] })).rejects.toThrow(
			/boom/,
		);
	});
});
