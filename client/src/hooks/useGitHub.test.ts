import { describe, expect, it, vi } from "vitest";

const { authFetch, generateUUID, apiPost } = vi.hoisted(() => ({
	authFetch: vi.fn(),
	apiPost: vi.fn(),
	generateUUID: vi.fn(() => "generated-job"),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {},
	apiClient: { POST: apiPost },
	authFetch,
}));
vi.mock("@/lib/uuid", () => ({ generateUUID }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: vi.fn() }));

import { useSync, validateGitHubToken } from "./useGitHub";

describe("useSync", () => {
	it("submits a durable retry job id instead of a client-built retry plan", async () => {
		authFetch.mockResolvedValue({
			ok: true,
			json: async () => ({ job_id: "queued-job", status: "queued" }),
		});

		await useSync().mutateAsync("new-job", { retry_job_id: "failed-job" });

		expect(authFetch).toHaveBeenCalledWith("/api/github/sync", {
			method: "POST",
			body: JSON.stringify({
				job_id: "new-job",
				retry_job_id: "failed-job",
			}),
		});
	});
});

describe("validateGitHubToken", () => {
	it("preserves the API status and rate-limit explanation", async () => {
		const detail =
			"GitHub token validation failed (HTTP 403): GitHub rate limit exceeded. Resets at: 1793126584";
		apiPost.mockResolvedValue({ error: { detail } });
		await expect(validateGitHubToken("synthetic-token")).rejects.toThrow(
			detail,
		);
	});

	it("retains a useful message for an unrecognized API error", async () => {
		apiPost.mockResolvedValue({ error: {} });
		await expect(validateGitHubToken("synthetic-token")).rejects.toThrow(
			"Failed to validate token",
		);
	});
});
