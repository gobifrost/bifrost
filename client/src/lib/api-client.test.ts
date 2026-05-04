import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient, authFetch } from "./api-client";
import { ACCESS_TOKEN_KEY } from "./auth-token";

/**
 * Build a mock Response with a given status and an empty body.
 */
function mockResponse(status: number): Response {
	return new Response(null, { status });
}

/**
 * Build a never-expiring access token so `ensureValidToken` short-circuits
 * without actually hitting the refresh endpoint.
 *
 * JWT shape: header.payload.signature (signature is unverified by the
 * client). The payload is `{exp: <far-future>}`.
 */
function buildFakeToken(): string {
	const farFuture = Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 365;
	const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
	const payload = btoa(JSON.stringify({ exp: farFuture }));
	return `${header}.${payload}.sig`;
}

/**
 * Drain the microtask queue + advance fake timers so any pending setTimeout
 * for retry backoff fires. Repeated multiple times to flush
 * await-then-setTimeout chains.
 */
async function flushRetries(maxBackoffMs: number = 5000): Promise<void> {
	// Run microtasks so awaited fetch promises resolve before timers tick.
	for (let i = 0; i < 5; i++) {
		await Promise.resolve();
	}
	await vi.advanceTimersByTimeAsync(maxBackoffMs);
}

describe("authFetch transient 5xx retry", () => {
	let fetchMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		// Seed a valid access token so ensureValidToken passes without a
		// refresh call.
		localStorage.setItem(ACCESS_TOKEN_KEY, buildFakeToken());

		fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);

		// Use fake timers so the backoff sleeps don't actually delay tests.
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
	});

	it("retries a GET that returns 503 twice then 200", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(200));

		const promise = authFetch("/api/test");
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(3);
	});

	it("does not retry a POST that returns 503", async () => {
		fetchMock.mockResolvedValueOnce(mockResponse(503));

		const promise = authFetch("/api/test", { method: "POST" });
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(503);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("returns the last 503 after a PUT exhausts all retries", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503));

		const promise = authFetch("/api/test", {
			method: "PUT",
			body: JSON.stringify({ x: 1 }),
		});
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(503);
		// 1 initial attempt + 3 retries
		expect(fetchMock).toHaveBeenCalledTimes(4);
	});

	it("does not retry a GET that returns 200 immediately", async () => {
		fetchMock.mockResolvedValueOnce(mockResponse(200));

		const promise = authFetch("/api/test");
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("retries a GET through 502 then 503 then resolves to 200", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(502))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(200));

		const promise = authFetch("/api/test");
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(3);
	});

	it("retries a DELETE on 504 and resolves to 204", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(504))
			.mockResolvedValueOnce(mockResponse(204));

		const promise = authFetch("/api/test", { method: "DELETE" });
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(204);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});

	it("does not retry a GET that returns 500 (not in TRANSIENT_5XX)", async () => {
		fetchMock.mockResolvedValueOnce(mockResponse(500));

		const promise = authFetch("/api/test");
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(500);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("retries a HEAD on 503 and resolves to 200", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(200));

		const promise = authFetch("/api/test", { method: "HEAD" });
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});
});

/**
 * Build a JSON Response with the given status and body.
 *
 * openapi-fetch tries to parse the response body when content-type is JSON,
 * so 200 responses need a real JSON payload — even an empty object — or
 * the parse step will throw.
 */
function mockJsonResponse(status: number, body: unknown = {}): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

describe("apiClient (openapi-fetch middleware) transient 5xx retry", () => {
	let fetchMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		// Seed a valid access token so the middleware's ensureValidToken
		// short-circuits without trying to refresh.
		localStorage.setItem(ACCESS_TOKEN_KEY, buildFakeToken());

		fetchMock = vi.fn();
		// openapi-fetch captures `globalThis.fetch` at createClient() time
		// (so module load), so stubGlobal alone won't hit the initial
		// request. The retry path inside the middleware DOES call global
		// `fetch`, which we still want intercepted — but we also pass
		// `fetch: fetchMock` per-call below so the initial request goes
		// through the same mock.
		vi.stubGlobal("fetch", fetchMock);

		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
	});

	it("apiClient.GET retries a transient 5xx and returns the eventual 200", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockJsonResponse(200, { version: "1.0" }));

		// `fetch` per-call override is supported by openapi-fetch's
		// coreFetch (`fetch = baseFetch` at line 48 of node_modules/openapi-fetch/src/index.js).
		// Cast to any since the typed FetchOptions doesn't surface the
		// override field in the public types but it's implemented in core.
		const promise = apiClient.GET("/api/version", {
			fetch: fetchMock,
		} as never);
		await flushRetries();
		const { response } = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});

	it("apiClient.PUT replays the body across retries via the WeakMap clone cache", async () => {
		// Capture the body sent on each fetch call. The body is a stream so
		// we read it eagerly in the mock impl before resolving.
		const capturedBodies: string[] = [];
		fetchMock.mockImplementation(async (req: Request) => {
			capturedBodies.push(await req.clone().text());
			// First attempt 503, second attempt 200.
			if (capturedBodies.length === 1) return mockResponse(503);
			return mockJsonResponse(200, {});
		});

		const promise = apiClient.PUT("/api/branding", {
			body: { primary_color: "#abcdef" },
			// See note above re: per-call fetch override + cast.
			fetch: fetchMock,
		} as never);
		await flushRetries();
		const { response } = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(2);
		// Both attempts must have carried the same body — proves the
		// WeakMap-cached pre-send clone replayed correctly.
		expect(capturedBodies).toHaveLength(2);
		expect(JSON.parse(capturedBodies[0])).toEqual({
			primary_color: "#abcdef",
		});
		expect(JSON.parse(capturedBodies[1])).toEqual({
			primary_color: "#abcdef",
		});
	});
});
