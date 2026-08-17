import { beforeEach, describe, expect, it, vi } from "vitest";

const mockAuthFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

import {
	deleteBuilderRunnerSetup,
	getBuilderRunnerSetup,
	provisionBuilderRunner,
	saveBuilderRunnerSetup,
} from "./builderRunner";

function response(body: unknown, status = 200) {
	return {
		ok: status >= 200 && status < 300,
		status,
		json: () => Promise.resolve(body),
	};
}

beforeEach(() => mockAuthFetch.mockReset());

describe("Builder runner service", () => {
	it("loads masked setup state", async () => {
		mockAuthFetch.mockResolvedValue(
			response({ readiness: { ready: false }, runner_image: "runner:v1" }),
		);
		const setup = await getBuilderRunnerSetup();
		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/admin/builder/runner",
			{ signal: undefined },
		);
		expect(setup.runner_image).toBe("runner:v1");
	});

	it("saves provider settings and provisions through the durable job endpoint", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(response({ provider: "cloudflare" }))
			.mockResolvedValueOnce(response({ job_id: "job-1", status: "queued" }, 202));

		await saveBuilderRunnerSetup({
			provider: "cloudflare",
			enabled: false,
			cloudflare: {
				account_id: "account",
				api_token: "secret",
			},
		});
		const job = await provisionBuilderRunner();

		expect(mockAuthFetch.mock.calls[0][1]).toMatchObject({ method: "PUT" });
		expect(mockAuthFetch).toHaveBeenLastCalledWith(
			"/api/admin/builder/runner/provision",
			{ method: "POST" },
		);
		expect(job.job_id).toBe("job-1");
	});

	it("removes saved configuration", async () => {
		mockAuthFetch.mockResolvedValue({ ok: true, status: 204 });
		await expect(deleteBuilderRunnerSetup()).resolves.toBeUndefined();
		expect(mockAuthFetch).toHaveBeenCalledWith("/api/admin/builder/runner", {
			method: "DELETE",
		});
	});
});
