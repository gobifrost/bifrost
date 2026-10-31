import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient, authFetch, setRefusedChangeListener } from "./api-client";
import {
	ACCESS_TOKEN_KEY,
	clearAuthTokens,
	setImpersonationToken,
} from "./auth-token";

interface TestPlatformAuthBridge {
	getAccessToken: () => string | null;
	canRefreshAccessToken: () => boolean;
	refreshAccessToken: () => Promise<boolean>;
	handleAuthenticationFailure: () => void;
}

type TestPlatformAuthGlobal = typeof globalThis & {
	__BIFROST_PLATFORM_AUTH_V1__?: TestPlatformAuthBridge;
};

function installSerialWebLocks(): () => void {
	const previousLocks = Object.getOwnPropertyDescriptor(navigator, "locks");
	let tail = Promise.resolve();
	const request = vi.fn(
		async <T>(_name: string, callback: () => Promise<T>): Promise<T> => {
			const previous = tail;
			let release!: () => void;
			tail = new Promise<void>((resolve) => {
				release = resolve;
			});
			await previous;
			try {
				return await callback();
			} finally {
				release();
			}
		},
	);
	Object.defineProperty(navigator, "locks", {
		configurable: true,
		value: { request },
	});
	return () => {
		if (previousLocks) Object.defineProperty(navigator, "locks", previousLocks);
		else delete (navigator as unknown as { locks?: unknown }).locks;
	};
}

let restoreWebLocks: (() => void) | null = null;

beforeEach(() => {
	restoreWebLocks = installSerialWebLocks();
});

afterEach(() => {
	restoreWebLocks?.();
	restoreWebLocks = null;
});

/**
 * Build a mock Response with a given status and an empty body.
 */
function mockResponse(status: number): Response {
	return new Response(null, { status });
}

function refreshRequiredResponse(): Response {
	return new Response(null, {
		status: 401,
		headers: {
			"WWW-Authenticate":
				'Bearer error="invalid_token", error_description="refresh_required"',
		},
	});
}

/**
 * Build a never-expiring access token so `ensureValidToken` short-circuits
 * without actually hitting the refresh endpoint.
 *
 * JWT shape: header.payload.signature (signature is unverified by the
 * client). The payload is `{exp: <far-future>}`.
 */
function buildFakeToken(marker = ""): string {
	const farFuture = Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 365;
	const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
	const payload = btoa(JSON.stringify({ exp: farFuture, marker }));
	return `${header}.${payload}.sig`;
}

describe("V2 app platform auth bridge", () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
	});

	it("shares one refresh request across concurrent SDK recovery calls", async () => {
		const refreshedToken = buildFakeToken();
		let releaseRefresh!: () => void;
		const refreshGate = new Promise<void>((resolve) => {
			releaseRefresh = resolve;
		});
		const fetchMock = vi.fn(async () => {
			await refreshGate;
			return mockJsonResponse(200, { access_token: refreshedToken });
		});
		vi.stubGlobal("fetch", fetchMock);
		const bridge = (globalThis as TestPlatformAuthGlobal)
			.__BIFROST_PLATFORM_AUTH_V1__;
		expect(bridge).toBeDefined();

		const first = bridge!.refreshAccessToken();
		const second = bridge!.refreshAccessToken();
		await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
		releaseRefresh();

		await expect(Promise.all([first, second])).resolves.toEqual([
			true,
			true,
		]);
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(bridge!.getAccessToken()).toBe(refreshedToken);
	});

	it("adopts a token refreshed by another tab while waiting for the browser lock", async () => {
		const refreshedToken = buildFakeToken();
		const request = vi.fn(async (_name: string, callback: () => Promise<boolean>) => {
			localStorage.setItem(ACCESS_TOKEN_KEY, refreshedToken);
			return callback();
		});
		Object.defineProperty(navigator, "locks", {
			configurable: true,
			value: { request },
		});
		const fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
		const bridge = (globalThis as TestPlatformAuthGlobal)
			.__BIFROST_PLATFORM_AUTH_V1__;

		await expect(bridge!.refreshAccessToken()).resolves.toBe(true);
		expect(request).toHaveBeenCalledWith("bifrost.auth.refresh", expect.any(Function));
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it("refuses refresh when Web Locks are unavailable", async () => {
		delete (navigator as unknown as { locks?: unknown }).locks;
		const fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
		const bridge = (globalThis as TestPlatformAuthGlobal)
			.__BIFROST_PLATFORM_AUTH_V1__;

		await expect(bridge!.refreshAccessToken()).resolves.toBe(false);
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it("treats a token cleared while waiting for the lock as an ended session", async () => {
		const request = vi.fn(async (_name: string, callback: () => Promise<boolean>) => {
			localStorage.removeItem(ACCESS_TOKEN_KEY);
			return callback();
		});
		Object.defineProperty(navigator, "locks", {
			configurable: true,
			value: { request },
		});
		localStorage.setItem(ACCESS_TOKEN_KEY, buildFakeToken());
		const fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
		const bridge = (globalThis as TestPlatformAuthGlobal)
			.__BIFROST_PLATFORM_AUTH_V1__;

		await expect(bridge!.refreshAccessToken()).resolves.toBe(false);
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it("does not publish a refresh that completes after logout", async () => {
		const token = buildFakeToken("before-logout");
		localStorage.setItem(ACCESS_TOKEN_KEY, token);
		let resolveJson!: (value: { access_token: string }) => void;
		const body = new Promise<{ access_token: string }>((resolve) => {
			resolveJson = resolve;
		});
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: () => body,
		} as Response);
		vi.stubGlobal("fetch", fetchMock);
		const refreshed = vi.fn();
		window.addEventListener("bifrost:access-token-refreshed", refreshed);
		const bridge = (globalThis as TestPlatformAuthGlobal)
			.__BIFROST_PLATFORM_AUTH_V1__;

		try {
			const refresh = bridge!.refreshAccessToken();
			await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
			clearAuthTokens();
			resolveJson({ access_token: buildFakeToken("should-not-publish") });

			await expect(refresh).resolves.toBe(false);
			expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
			expect(refreshed).not.toHaveBeenCalled();
		} finally {
			window.removeEventListener("bifrost:access-token-refreshed", refreshed);
		}
	});
});

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

	it("shares the three-replay budget between refresh recovery and transient retries", async () => {
		fetchMock
			.mockResolvedValueOnce(refreshRequiredResponse())
			.mockResolvedValueOnce(mockJsonResponse(200, { access_token: buildFakeToken() }))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503));

		const responsePromise = authFetch("/api/test");
		await flushRetries();

		await expect(responsePromise).resolves.toMatchObject({ status: 503 });
		// Initial request + refresh + two remaining retries after the challenge.
		expect(fetchMock).toHaveBeenCalledTimes(5);
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

describe.each(["authFetch", "apiClient"] as const)(
	"%s authentication challenges",
	(kind) => {
		let fetchMock: ReturnType<typeof vi.fn>;
		let token: string;
		beforeEach(() => {
			token = buildFakeToken();
			localStorage.setItem(ACCESS_TOKEN_KEY, token);
			window.history.replaceState(null, "", "/login");
			fetchMock = vi.fn();
			vi.stubGlobal("fetch", fetchMock);
		});
		afterEach(() => {
			vi.unstubAllGlobals();
			localStorage.clear();
			sessionStorage.clear();
			window.history.replaceState(null, "", "/");
		});
		const call = async () =>
			kind === "authFetch"
				? authFetch("http://localhost/api/version")
				: (
						await apiClient.GET("/api/version", {
							fetch: fetchMock,
						} as never)
					).response;
		it("does not replay a generic 401", async () => {
			fetchMock.mockResolvedValueOnce(mockResponse(401));
			expect((await call()).status).toBe(401);
			expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(token);
			expect(fetchMock).toHaveBeenCalledTimes(1);
		});
		it("leaves 403 permission failures to the page", async () => {
			fetchMock.mockResolvedValueOnce(mockResponse(403));
			expect((await call()).status).toBe(403);
			expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(token);
			expect(fetchMock).toHaveBeenCalledTimes(1);
		});
		it("refreshes and retries only after the exact challenge", async () => {
			fetchMock
				.mockResolvedValueOnce(refreshRequiredResponse())
				.mockResolvedValueOnce(
					mockJsonResponse(200, { access_token: token }),
				)
				.mockResolvedValueOnce(
					mockJsonResponse(200, { version: "1.0" }),
				);
			expect((await call()).status).toBe(200);
			expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/refresh");
			expect(fetchMock).toHaveBeenCalledTimes(3);
		});
		it("clears a session when a challenged refresh fails", async () => {
			fetchMock
				.mockResolvedValueOnce(refreshRequiredResponse())
				.mockResolvedValueOnce(mockResponse(401));
			expect((await call()).status).toBe(401);
			expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
		});
	},
);

it("replays a challenged POST once before its endpoint effect runs", async () => {
	const token = buildFakeToken();
	localStorage.setItem(ACCESS_TOKEN_KEY, token);
	let requests = 0;
	let effects = 0;
	const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
		if (String(input) === "/api/auth/refresh")
			return mockJsonResponse(200, { access_token: token });
		requests += 1;
		if (requests === 1) return refreshRequiredResponse();
		effects += 1;
		return mockJsonResponse(200, { created: true });
	});
	vi.stubGlobal("fetch", fetchMock);

	await expect(authFetch("/api/widgets", { method: "POST", body: "{}" })).resolves.toMatchObject({ status: 200 });
	expect(effects).toBe(1);
	expect(fetchMock).toHaveBeenCalledTimes(3);
});

it("refreshes again when a second epoch challenge follows a rotated token", async () => {
	const firstToken = buildFakeToken("first");
	const secondToken = buildFakeToken("second");
	const thirdToken = buildFakeToken("third");
	localStorage.setItem(ACCESS_TOKEN_KEY, firstToken);
	const seenAuthorizations: string[] = [];
	let endpointAttempts = 0;
	const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		if (typeof input === "string" && input === "/api/auth/refresh") {
			return Response.json({
				access_token: seenAuthorizations.length === 1 ? secondToken : thirdToken,
			});
		}
		const headers = input instanceof Request ? input.headers : new Headers(init?.headers);
		seenAuthorizations.push(headers.get("Authorization") ?? "");
		endpointAttempts += 1;
		return endpointAttempts < 3
			? refreshRequiredResponse()
			: mockJsonResponse(200, { ok: true });
	});
	vi.stubGlobal("fetch", fetchMock);

	await expect(authFetch("/api/epoch-sensitive")).resolves.toMatchObject({ status: 200 });
	expect(seenAuthorizations).toEqual([
		`Bearer ${firstToken}`,
		`Bearer ${secondToken}`,
		`Bearer ${thirdToken}`,
	]);
	expect(fetchMock.mock.calls.filter(([input]) => input === "/api/auth/refresh")).toHaveLength(2);
});

describe("impersonation authentication failures", () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
	});

	it("does not refresh or replay a rejected mutation as the operator", async () => {
		const operator = buildFakeToken();
		localStorage.setItem(ACCESS_TOKEN_KEY, operator);
		setImpersonationToken(buildFakeToken());
		const fetchMock = vi.fn().mockResolvedValue(mockResponse(401));
		vi.stubGlobal("fetch", fetchMock);

		const response = await authFetch("/api/users/user-1", {
			method: "PATCH",
			body: JSON.stringify({ name: "No replay" }),
		});

		expect(response.status).toBe(401);
		expect(fetchMock).toHaveBeenCalledOnce();
		expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(operator);
	});
});

describe("apiClient refused changes", () => {
	let fetchMock: ReturnType<typeof vi.fn>;
	beforeEach(() => {
		localStorage.setItem(ACCESS_TOKEN_KEY, buildFakeToken());
		fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
	});
	afterEach(() => {
		setRefusedChangeListener(null);
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
		localStorage.clear();
	});

	it("tells the listener when a change is refused", async () => {
		const listener = vi.fn();
		setRefusedChangeListener(listener);
		fetchMock.mockResolvedValueOnce(
			mockJsonResponse(403, {
				detail: "You don't have permission to manage users",
			}),
		);

		const { response } = await apiClient.PATCH("/api/users/{user_id}", {
			params: { path: { user_id: "u1" } },
			body: { name: "New" },
			fetch: fetchMock,
		} as never);

		expect(response.status).toBe(403);
		expect(listener).toHaveBeenCalledOnce();
	});

	it("stays quiet when a read is refused", async () => {
		const listener = vi.fn();
		setRefusedChangeListener(listener);
		fetchMock.mockResolvedValueOnce(mockResponse(403));

		await apiClient.GET("/api/version", { fetch: fetchMock } as never);

		expect(listener).not.toHaveBeenCalled();
	});
});
