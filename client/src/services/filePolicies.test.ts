import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api-client", () => ({ authFetch: vi.fn() }));
import { authFetch } from "@/lib/api-client";
import {
	effectiveAccess,
	listFilePolicies,
	saveFilePolicy,
	testAllActions,
} from "./filePolicies";

function jsonResponse(body: unknown) {
	return { ok: true, status: 200, json: async () => body } as Response;
}

describe("effectiveAccess", () => {
	beforeEach(() => vi.mocked(authFetch).mockReset());
	it("returns matching policies longest-prefix first", async () => {
		vi.mocked(authFetch).mockResolvedValue(
			jsonResponse({
				policies: [
					{
						id: "1",
						location: "gallery",
						path: "",
						organization_id: null,
						policies: { policies: [] },
					},
					{
						id: "2",
						location: "gallery",
						path: "team/",
						organization_id: null,
						policies: { policies: [] },
					},
					{
						id: "3",
						location: "gallery",
						path: "other/",
						organization_id: null,
						policies: { policies: [] },
					},
				],
			}),
		);
		const result = await effectiveAccess("gallery", "team/pic.png", null);
		expect(result.map((p) => p.id)).toEqual(["2", "1"]);
	});
});

describe("solution policy context", () => {
	beforeEach(() => vi.mocked(authFetch).mockReset());

	it("threads the install separately from the ordinary organization scope", async () => {
		vi.mocked(authFetch).mockResolvedValue(jsonResponse({ policies: [] }));

		await listFilePolicies({ scope: "org-1", solution: "solution-1" });

		expect(authFetch).toHaveBeenCalledWith(
			"/api/files/policies?organization_id=org-1&solution=solution-1",
		);
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

	it("keeps an explicit solution context on every policy test request", async () => {
		vi.mocked(authFetch).mockResolvedValue(
			jsonResponse({
				allowed: true,
				path: "p",
				location: "gallery",
				action: "read",
			}),
		);

		await testAllActions({
			location: "gallery",
			path: "p",
			scope: "org-1",
			userId: "u",
			solution: "solution-1",
		});

		expect(authFetch).toHaveBeenCalledTimes(4);
		expect(
			vi
				.mocked(authFetch)
				.mock.calls.every(
					([url]) =>
						url === "/api/files/policies/test?solution=solution-1",
				),
		).toBe(true);
	});
});

describe("saveFilePolicy", () => {
	beforeEach(() => vi.mocked(authFetch).mockReset());

	it("throws the API error detail when saving fails", async () => {
		vi.mocked(authFetch).mockResolvedValue({
			ok: false,
			status: 400,
			statusText: "Bad Request",
			json: async () => ({ detail: "Policy is invalid" }),
		} as Response);

		await expect(
			saveFilePolicy({
				location: "gallery",
				path: "team/",
				policies: { policies: [] },
			}),
		).rejects.toThrow("Policy is invalid");
	});
});
