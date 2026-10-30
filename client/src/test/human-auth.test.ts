import { describe, expect, it, vi } from "vitest";
import {
	createChallengeAwareApi,
	createFreshApiContext,
} from "../../e2e/fixtures/human-auth";
import { REQUEST_REPLAY_BUDGET } from "../lib/app-sdk/transport";

function response(
	status: number,
	headers: Record<string, string> = {},
	body: Record<string, unknown> = { mfa_required: false },
) {
	return {
		status: () => status,
		headers: () => headers,
		ok: () => status >= 200 && status < 300,
		text: async () => "",
		json: async () => body,
	} as never;
}

const refreshRequired = () =>
	response(401, {
		"www-authenticate":
			'Bearer error="invalid_token", error_description="refresh_required"',
	});

function fakeContext(getGeneration: () => string) {
	return {
		cookies: vi.fn(async () => [
			{ name: "access_token", value: getGeneration() },
			{ name: "refresh_token", value: `refresh-${getGeneration()}` },
			{ name: "csrf_token", value: `csrf-${getGeneration()}` },
		]),
	} as never;
}

describe("challenge-aware E2E API context", () => {
	it("logs every API consumer into an independent cookie context", async () => {
		const contexts = ["first", "second"].map((generation) => ({
			request: {
				post: vi
					.fn()
					.mockResolvedValueOnce(
						response(200, {}, { mfa_required: true, mfa_token: "mfa" }),
					)
					.mockResolvedValueOnce(response(200)),
				fetch: vi.fn(async () => response(200)),
			},
			cookies: vi.fn(async () => [
				{ name: "access_token", value: generation },
				{ name: "refresh_token", value: `refresh-${generation}` },
			]),
			close: vi.fn(),
		}));
		const browser = {
			newContext: vi
				.fn()
				.mockResolvedValueOnce(contexts[0])
				.mockResolvedValueOnce(contexts[1]),
		};
		const credentials = {
			email: "human@example.test",
			password: "password",
			name: "Human",
			totpSecret: "JBSWY3DPEHPK3PXP",
			userId: "human",
			accessToken: "unused",
			refreshToken: "unused",
			isSuperuser: false,
		};

		const first = await createFreshApiContext(browser as never, credentials);
		const second = await createFreshApiContext(browser as never, credentials);

		expect(browser.newContext).toHaveBeenCalledTimes(2);
		expect(contexts[0].request.post).toHaveBeenCalledTimes(2);
		expect(contexts[1].request.post).toHaveBeenCalledTimes(2);
		expect(first.context).not.toBe(second.context);
		expect((await first.context.cookies())[0].value).toBe("first");
		expect((await second.context.cookies())[0].value).toBe("second");
	});

	it("replays only after the exact refresh challenge", async () => {
		let generation = "before";
		const raw = {
			fetch: vi.fn(async (url: string) => {
				if (url === "/api/auth/refresh") {
					generation = "after";
					return response(200);
				}
				return raw.fetch.mock.calls.filter(([called]) => called === "/api/widgets")
					.length === 1
					? refreshRequired()
					: response(200);
			}),
		};
		const api = createChallengeAwareApi(raw as never, fakeContext(() => generation));

		await expect(api.get("/api/widgets")).resolves.toMatchObject({});
		expect(raw.fetch.mock.calls.map(([url]) => url)).toEqual([
			"/api/widgets",
			"/api/auth/refresh",
			"/api/widgets",
		]);
	});

	it("does not refresh or replay a generic 401", async () => {
		const raw = { fetch: vi.fn(async () => response(401)) };
		const api = createChallengeAwareApi(raw as never, fakeContext(() => "stable"));

		const result = await api.get("/api/widgets");
		expect(result.status()).toBe(401);
		expect(raw.fetch).toHaveBeenCalledTimes(1);
	});

	it("preserves a multipart upload and renews its CSRF header on replay", async () => {
		let generation = "before";
		const raw = {
			fetch: vi.fn(async (url: string, _options: unknown) => {
				if (url === "/api/auth/refresh") {
					generation = "after";
					return response(200);
				}
				return generation === "before" ? refreshRequired() : response(202);
			}),
		};
		const api = createChallengeAwareApi(raw as never, fakeContext(() => generation));
		const multipart = {
			file: { name: "solution.zip", mimeType: "application/zip", buffer: Buffer.from("zip") },
		};

		const result = await api.post("/api/solutions/id/deploy", {
			multipart,
			params: { force: true },
			headers: { "X-Upload-Trace": "fixture" },
		});

		expect(result.status()).toBe(202);
		const uploads = raw.fetch.mock.calls.filter(([url]) => url.endsWith("/deploy"));
		expect(uploads).toHaveLength(2);
		for (const [, options] of uploads) {
			expect(options).toMatchObject({ multipart, params: { force: true }, method: "POST" });
		}
		expect(uploads[0][1]).toMatchObject({ headers: { "X-CSRF-Token": "csrf-before", "X-Upload-Trace": "fixture" } });
		expect(uploads[1][1]).toMatchObject({ headers: { "X-CSRF-Token": "csrf-after", "X-Upload-Trace": "fixture" } });
	});

	it("stops after the shared replay budget for persistent refresh challenges", async () => {
		let generation = 0;
		const raw = {
			fetch: vi.fn(async (url: string) => {
				if (url === "/api/auth/refresh") {
					generation += 1;
					return response(200);
				}
				return refreshRequired();
			}),
		};
		const api = createChallengeAwareApi(
			raw as never,
			fakeContext(() => `generation-${generation}`),
		);

		const result = await api.get("/api/widgets");

		expect(result.status()).toBe(401);
		expect(
			raw.fetch.mock.calls.filter(([url]) => url === "/api/auth/refresh"),
		).toHaveLength(REQUEST_REPLAY_BUDGET);
		expect(
			raw.fetch.mock.calls.filter(([url]) => url === "/api/widgets"),
		).toHaveLength(REQUEST_REPLAY_BUDGET + 1);
	});

	it("single-flights concurrent refreshes and adopts the rotated generation", async () => {
		let generation = "before";
		let releaseRefresh!: () => void;
		const refreshGate = new Promise<void>((resolve) => {
			releaseRefresh = resolve;
		});
		const raw = {
			fetch: vi.fn(async (url: string) => {
				if (url === "/api/auth/refresh") {
					await refreshGate;
					generation = "after";
					return response(200);
				}
				return generation === "before" ? refreshRequired() : response(200);
			}),
		};
		const api = createChallengeAwareApi(raw as never, fakeContext(() => generation));

		const first = api.get("/api/one");
		const second = api.get("/api/two");
		await vi.waitFor(() =>
			expect(
				raw.fetch.mock.calls.filter(([url]) => url === "/api/auth/refresh"),
			).toHaveLength(1),
		);
		releaseRefresh();

		await expect(Promise.all([first, second])).resolves.toHaveLength(2);
		expect(
			raw.fetch.mock.calls.filter(([url]) => url === "/api/auth/refresh"),
		).toHaveLength(1);
	});
});
