import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api-client", () => ({ authFetch: vi.fn() }));
import { authFetch } from "@/lib/api-client";
import { effectiveAccess, testAllActions } from "./filePolicies";

function jsonResponse(body: unknown) {
	return { ok: true, status: 200, json: async () => body } as Response;
}

describe("effectiveAccess", () => {
	beforeEach(() => vi.mocked(authFetch).mockReset());
	it("returns matching policies longest-prefix first", async () => {
		vi.mocked(authFetch).mockResolvedValue(
			jsonResponse({
				policies: [
					{ id: "1", location: "gallery", path: "", organization_id: null, policies: { policies: [] } },
					{ id: "2", location: "gallery", path: "team/", organization_id: null, policies: { policies: [] } },
					{ id: "3", location: "gallery", path: "other/", organization_id: null, policies: { policies: [] } },
				],
			}),
		);
		const result = await effectiveAccess("gallery", "team/pic.png", null);
		expect(result.map((p) => p.id)).toEqual(["2", "1"]);
	});
});

describe("testAllActions", () => {
	beforeEach(() => vi.mocked(authFetch).mockReset());
	it("collects all four actions", async () => {
		vi.mocked(authFetch).mockImplementation(async (_url, init = {}) => {
			const body = (init as RequestInit).body;
			const action = body ? JSON.parse(body as string).action : "read";
			return jsonResponse({
				allowed: action === "read",
				path: "p",
				location: "gallery",
				action,
			});
		});
		const result = await testAllActions({
			location: "gallery",
			path: "p",
			scope: null,
			userId: "u",
		});
		expect(result.read.allowed).toBe(true);
		expect(result.write.allowed).toBe(false);
		expect(Object.keys(result).sort()).toEqual([
			"delete",
			"list",
			"read",
			"write",
		]);
	});
});
