import { describe, it, expect, vi, beforeEach } from "vitest";
import { listShares, listStructure } from "./fileStructure";

vi.mock("@/lib/api-client", () => ({
	authFetch: vi.fn(),
}));
import { authFetch } from "@/lib/api-client";

function jsonResponse(body: unknown) {
	return { ok: true, status: 200, json: async () => body } as Response;
}

describe("fileStructure service", () => {
	beforeEach(() => vi.mocked(authFetch).mockReset());

	it("listShares maps snake_case to camelCase", async () => {
		vi.mocked(authFetch).mockResolvedValue(
			jsonResponse({
				shares: [{ location: "gallery", read_only: false, has_policy: true }],
			}),
		);
		const shares = await listShares(null);
		expect(shares[0]).toEqual({
			location: "gallery",
			readOnly: false,
			hasPolicy: true,
		});
		const [, init] = vi.mocked(authFetch).mock.calls[0];
		expect(JSON.parse((init as RequestInit).body as string)).toEqual({
			scope: null,
		});
	});

	it("listStructure sends location + prefix + scope", async () => {
		vi.mocked(authFetch).mockResolvedValue(
			jsonResponse({
				entries: [{ name: "a.png", kind: "file", path: "a.png" }],
			}),
		);
		const entries = await listStructure("gallery", "sub", "org-1");
		expect(entries[0]).toEqual({ name: "a.png", kind: "file", path: "a.png" });
		const [, init] = vi.mocked(authFetch).mock.calls[0];
		expect(JSON.parse((init as RequestInit).body as string)).toEqual({
			location: "gallery",
			prefix: "sub",
			scope: "org-1",
		});
	});

	it("throws on non-ok response with detail", async () => {
		vi.mocked(authFetch).mockResolvedValue({
			ok: false,
			status: 403,
			json: async () => ({ detail: "Forbidden" }),
		} as Response);
		await expect(listShares(null)).rejects.toThrow("Forbidden");
	});
});
