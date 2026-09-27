import { describe, expect, it, vi } from "vitest";

const { authFetch, generateUUID } = vi.hoisted(() => ({
	authFetch: vi.fn(),
	generateUUID: vi.fn(() => "generated-job"),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {},
	apiClient: {},
	authFetch,
}));
vi.mock("@/lib/uuid", () => ({ generateUUID }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: vi.fn() }));

import { useSync } from "./useGitHub";

describe("useSync", () => {
	it("submits a durable retry job id instead of a client-built retry plan", async () => {
		authFetch.mockResolvedValue({
			ok: true,
			json: async () => ({ job_id: "queued-job", status: "queued" }),
		});

		await useSync().mutateAsync("new-job", { retry_job_id: "failed-job" });

		expect(authFetch).toHaveBeenCalledWith("/api/github/sync", {
			method: "POST",
			body: JSON.stringify({ job_id: "new-job", retry_job_id: "failed-job" }),
		});
	});
});
