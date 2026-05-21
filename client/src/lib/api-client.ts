/**
 * Type-safe API client using openapi-fetch and openapi-react-query
 * Includes CSRF protection for cookie-based authentication
 *
 * Usage:
 * - $api.useQuery("get", "/api/endpoint") for queries in components
 * - $api.useMutation("post", "/api/endpoint") for mutations in components
 * - apiClient.GET/POST/etc for imperative usage outside React
 */

import createClient from "openapi-fetch";
import createQueryClient from "openapi-react-query";
import type { paths } from "./v1";
import { parseApiError, ApiError, RateLimitError } from "./api-error";
import {
	ACCESS_TOKEN_KEY,
	clearAuthTokens,
	consumeEmbedTokenFromHash,
	getActiveToken,
	isEmbedSession,
} from "./auth-token";

// Pull an embed token off the URL fragment (if any) before any API call
// runs. The token is stored in sessionStorage rather than localStorage so
// it stays scoped to this tab/iframe and never overwrites a normal user
// session that happens to be open in another tab on the same origin.
consumeEmbedTokenFromHash();

// Buffer time before expiration to trigger refresh (60 seconds)
const TOKEN_REFRESH_BUFFER_SECONDS = 60;

// Transient 5xx statuses that warrant a client-side retry. Limited to the
// classic LB/proxy "pod just dropped" responses — 500/501/505 indicate
// server-side bugs we should surface, not paper over.
const TRANSIENT_5XX = new Set([502, 503, 504]);

// Retry only methods that are safe to replay. POST/PATCH may have already
// taken effect server-side even when the response was a 5xx; auto-retrying
// would create duplicate resources.
const IDEMPOTENT_METHODS = new Set([
	"GET",
	"PUT",
	"DELETE",
	"HEAD",
	"OPTIONS",
]);

// Backoff schedule (ms) between retry attempts. Up to 3 retries on top of
// the initial attempt, totaling ~3s of additional latency in the worst case.
const RETRY_BACKOFF_MS = [250, 750, 2000];

function isTransient5xx(status: number): boolean {
	return TRANSIENT_5XX.has(status);
}

function isIdempotent(method: string): boolean {
	return IDEMPOTENT_METHODS.has(method.toUpperCase());
}

/**
 * Wrap a fetch operation with retry on transient 5xx (502/503/504) for
 * idempotent methods. Non-idempotent requests (POST/PATCH) pass through
 * unchanged.
 *
 * The `doFetch` callback MUST produce a fresh Request each call — if a
 * Request with a body is reused, its body stream will be consumed after
 * the first attempt.
 */
async function withTransient5xxRetry(
	method: string,
	doFetch: () => Promise<Response>,
): Promise<Response> {
	if (!isIdempotent(method)) return doFetch();
	let response = await doFetch();
	for (const delay of RETRY_BACKOFF_MS) {
		if (!isTransient5xx(response.status)) return response;
		await new Promise((resolve) => setTimeout(resolve, delay));
		response = await doFetch();
	}
	// Out of retries — caller sees the last 5xx.
	return response;
}

/**
 * Continue retrying after an initial fetch already produced a transient 5xx.
 * Used in the openapi-fetch middleware paths where the framework fires the
 * first request for us, so we react to the response rather than wrapping the
 * full lifecycle.
 */
async function retryAfterTransient5xx(
	method: string,
	baseRequest: Request,
	initialResponse: Response,
): Promise<Response> {
	if (!isIdempotent(method)) return initialResponse;
	let response = initialResponse;
	for (const delay of RETRY_BACKOFF_MS) {
		if (!isTransient5xx(response.status)) return response;
		await new Promise((resolve) => setTimeout(resolve, delay));
		response = await fetch(baseRequest.clone());
	}
	return response;
}

/**
 * Cache of pre-send Request clones, keyed by the live Request object that
 * openapi-fetch hands to onRequest/onResponse. Stashed in onRequest before
 * the body stream is consumed so 5xx retry in onResponse can replay
 * requests with bodies (e.g. PUT).
 */
const _requestClones = new WeakMap<Request, Request>();

// Endpoints that should skip token refresh check
const AUTH_ENDPOINTS = [
	"/auth/login",
	"/auth/status",
	"/auth/refresh",
	"/api/auth/refresh",
	"/auth/oauth/callback",
	"/auth/mfa/login",
	"/auth/mfa/setup",
];

/**
 * Parse JWT payload without verification (server validates)
 */
function parseJwt(token: string): { exp?: number } | null {
	try {
		const base64Url = token.split(".")[1];
		if (!base64Url) return null;
		const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
		const jsonPayload = decodeURIComponent(
			atob(base64)
				.split("")
				.map(
					(c) =>
						"%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2),
				)
				.join(""),
		);
		return JSON.parse(jsonPayload);
	} catch {
		return null;
	}
}

/**
 * Check if token is expiring soon (within buffer period)
 */
function isTokenExpiringSoon(token: string): boolean {
	const payload = parseJwt(token);
	if (!payload || payload.exp === undefined) return true;
	return Date.now() >= (payload.exp - TOKEN_REFRESH_BUFFER_SECONDS) * 1000;
}

// Lock to prevent concurrent refresh attempts
let refreshPromise: Promise<boolean> | null = null;

/**
 * Refresh the access token using the refresh token cookie
 * Uses HttpOnly cookie for refresh token (more secure than localStorage)
 * Returns true if successful, false if refresh failed
 */
async function refreshAccessToken(): Promise<boolean> {
	// Use lock to prevent concurrent refresh attempts
	if (refreshPromise) {
		return refreshPromise;
	}

	refreshPromise = (async () => {
		try {
			// POST to refresh endpoint - browser sends refresh_token cookie automatically
			const res = await fetch("/api/auth/refresh", {
				method: "POST",
				credentials: "same-origin",
			});

			if (!res.ok) return false;

			const data = await res.json();
			if (data.access_token) {
				// Store access token in localStorage for proactive expiry checking
				localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
				return true;
			}
			return false;
		} catch {
			return false;
		}
	})().finally(() => {
		refreshPromise = null;
	});

	return refreshPromise;
}

/**
 * Retry a request after token refresh using current auth state.
 */
async function retryRequestWithFreshAuth(request: Request): Promise<Response> {
	const headers = new Headers(request.headers);
	const token = localStorage.getItem(ACCESS_TOKEN_KEY);

	if (token) {
		headers.set("Authorization", `Bearer ${token}`);
	} else {
		headers.delete("Authorization");
	}

	return fetch(
		new Request(request, {
			headers,
			credentials: "same-origin",
		}),
	);
}

/**
 * Handle authentication failure - clear session and redirect to login
 */
function handleAuthFailure(): void {
	sessionStorage.removeItem("userId");
	clearAuthTokens();

	const currentPath = window.location.pathname;
	if (
		currentPath !== "/login" &&
		currentPath !== "/setup" &&
		!currentPath.startsWith("/auth/callback")
	) {
		window.location.href = `/login?returnTo=${encodeURIComponent(currentPath)}`;
	}
}

/**
 * Ensure we have a valid (non-expiring) access token before making a request
 * Proactively refreshes token before it expires
 */
async function ensureValidToken(): Promise<boolean> {
	// Embed sessions can't refresh — the embed token is minted by the
	// HMAC handshake at /embed/apps/{slug} and there's no refresh cookie
	// for it. Just trust whatever's in sessionStorage; expiry surfaces
	// as a 401 and the caller can re-enter the embed flow.
	if (isEmbedSession()) return true;

	const token = localStorage.getItem(ACCESS_TOKEN_KEY);

	// No access token in localStorage, but HttpOnly refresh cookie may be valid
	if (!token) return refreshAccessToken();

	// Token is still valid - proceed
	if (!isTokenExpiringSoon(token)) return true;

	// Token is expiring soon - refresh it
	// Lock is handled inside refreshAccessToken
	return refreshAccessToken();
}

/**
 * Get CSRF token from cookie (set by backend on login/OAuth)
 * The csrf_token cookie is non-HttpOnly so JavaScript can read it
 */
export function getCsrfToken(): string | null {
	const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
	return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Check if request method requires CSRF protection
 */
function requiresCsrf(method: string): boolean {
	return ["POST", "PUT", "PATCH", "DELETE"].includes(method.toUpperCase());
}

// Create base client (internal - don't export directly)
// baseUrl is empty because OpenAPI paths already include /api prefix
const baseClient = createClient<paths>({
	baseUrl: "",
});

// Middleware to automatically inject user context headers,
// CSRF tokens, handle token refresh, and handle authentication errors
baseClient.use({
	async onRequest({ request }) {
		const url = new URL(request.url, window.location.origin);
		const isAuthEndpoint = AUTH_ENDPOINTS.some((ep) =>
			url.pathname.startsWith(ep),
		);

		// Skip token refresh for auth endpoints to avoid infinite loops
		if (!isAuthEndpoint) {
			const hasValidToken = await ensureValidToken();
			if (!hasValidToken) {
				// No valid token and refresh failed - redirect to login
				const currentPath = window.location.pathname;
				if (
					currentPath !== "/login" &&
					currentPath !== "/setup" &&
					!currentPath.startsWith("/auth/callback")
				) {
					window.location.href = `/login?returnTo=${encodeURIComponent(currentPath)}`;
				}
				// Throw to prevent the request from proceeding
				throw new Error("Authentication required");
			}
		}

		// Get user ID from session storage (set by auth provider)
		const userId = sessionStorage.getItem("userId");
		if (userId) {
			request.headers.set("X-User-Id", userId);
		}

		// Add CSRF token for mutating requests (cookie-based auth protection)
		if (requiresCsrf(request.method)) {
			const csrfToken = getCsrfToken();
			if (csrfToken) {
				request.headers.set("X-CSRF-Token", csrfToken);
			}
		}

		// Authentication: prefer HttpOnly cookie (sent automatically by browser).
		// Fall back to Bearer header from the active token store for embed
		// sessions (token arrives via URL fragment, not Set-Cookie) and for
		// any path where the cookie isn't being honored.
		if (!request.headers.has("Authorization")) {
			const token = getActiveToken();
			if (token) {
				request.headers.set("Authorization", `Bearer ${token}`);
			}
		}

		// Stash a pre-send clone so onResponse can replay this request on
		// transient 5xx (idempotent methods only — POST/PATCH bodies would
		// just hold memory we never use).
		if (isIdempotent(request.method)) {
			_requestClones.set(request, request.clone());
		}

		return request;
	},
	async onResponse({ request, response }) {
		// Handle 429 Too Many Requests - rate limited
		if (response.status === 429) {
			const retryAfter = parseInt(
				response.headers.get("Retry-After") || "60",
				10,
			);
			throw new RateLimitError(retryAfter);
		}

		// Handle 401 Unauthorized - attempt token refresh and retry
		if (response.status === 401) {
			// Skip retry for auth endpoints to prevent infinite loops
			const url = request.url;
			if (AUTH_ENDPOINTS.some((ep) => url.includes(ep))) {
				handleAuthFailure();
				return response;
			}

			// Attempt token refresh via cookie
			const refreshed = await refreshAccessToken();
			if (refreshed) {
				// Retry original request with current credentials
				return retryRequestWithFreshAuth(request);
			}

			// Refresh failed - redirect to login
			handleAuthFailure();
		}
		// 403 Forbidden = permission issue, don't redirect (user is authenticated)
		// Let the calling code handle displaying an appropriate error message

		// Retry transient 5xx (502/503/504) on idempotent methods. Rides
		// through brief windows during a rolling API deploy where a pod is
		// dropping out of the LB.
		if (isTransient5xx(response.status) && isIdempotent(request.method)) {
			const baseRequest = _requestClones.get(request) ?? request;
			return retryAfterTransient5xx(
				request.method,
				baseRequest,
				response,
			);
		}

		return response;
	},
});

/**
 * Raw API client with automatic header injection
 * Use for imperative calls outside React components
 * For React components, prefer $api.useQuery() and $api.useMutation()
 */
export const apiClient = baseClient;

/**
 * Type-safe React Query hooks from OpenAPI spec
 * Use in React components for automatic caching, refetching, and loading states
 *
 * @example
 * // Query
 * const { data, isLoading } = $api.useQuery("get", "/api/organizations");
 *
 * // Query with parameters
 * const { data } = $api.useQuery("get", "/api/organizations/{org_id}", {
 *   params: { path: { org_id: "123" } }
 * });
 *
 * // Mutation
 * const mutation = $api.useMutation("post", "/api/organizations");
 * mutation.mutate({ body: { name: "New Org" } });
 */
export const $api = createQueryClient(baseClient);

/**
 * Shared response handler for all clients
 * Handles 429 rate limiting and 401 authentication errors with retry
 */
async function handleAuthResponse(
	request: Request,
	response: Response,
): Promise<Response> {
	// Handle 429 Too Many Requests - rate limited
	if (response.status === 429) {
		const retryAfter = parseInt(
			response.headers.get("Retry-After") || "60",
			10,
		);
		throw new RateLimitError(retryAfter);
	}

	// Handle 401 Unauthorized - attempt token refresh and retry
	if (response.status === 401) {
		const url = request.url;
		if (!AUTH_ENDPOINTS.some((ep) => url.includes(ep))) {
			const refreshed = await refreshAccessToken();
			if (refreshed) {
				// Retry original request with current credentials
				return retryRequestWithFreshAuth(request);
			}
		}
		handleAuthFailure();
	}

	return response;
}

/**
 * Helper to override user context for admin operations
 */
export function withUserContext(userId: string) {
	const client = createClient<paths>({
		baseUrl: "",
	});

	client.use({
		async onRequest({ request }) {
			// Ensure valid token before request
			const hasValidToken = await ensureValidToken();
			if (!hasValidToken) {
				const currentPath = window.location.pathname;
				if (currentPath !== "/login" && currentPath !== "/setup") {
					window.location.href = `/login?returnTo=${encodeURIComponent(currentPath)}`;
				}
				throw new Error("Authentication required");
			}

			request.headers.set("X-User-Id", userId);

			// Add CSRF token for mutating requests
			if (requiresCsrf(request.method)) {
				const csrfToken = getCsrfToken();
				if (csrfToken) {
					request.headers.set("X-CSRF-Token", csrfToken);
				}
			}

			// Stash a pre-send clone for transient 5xx replay (idempotent
			// methods only).
			if (isIdempotent(request.method)) {
				_requestClones.set(request, request.clone());
			}

			return request;
		},
		async onResponse({ request, response }) {
			const finalResponse = await handleAuthResponse(request, response);
			if (
				isTransient5xx(finalResponse.status) &&
				isIdempotent(request.method)
			) {
				const baseRequest = _requestClones.get(request) ?? request;
				return retryAfterTransient5xx(
					request.method,
					baseRequest,
					finalResponse,
				);
			}
			return finalResponse;
		},
	});

	return client;
}

/**
 * Helper to handle openapi-fetch errors
 * Converts the error object to an ApiError with proper message extraction
 */
export function handleApiError(error: unknown): never {
	throw parseApiError(error);
}

// Re-export error classes for convenience
export { ApiError, RateLimitError, refreshAccessToken };

/**
 * Authenticated fetch wrapper for endpoints not in OpenAPI spec
 * Automatically injects context headers, CSRF tokens, and handles 401 responses
 * Auth is handled via HttpOnly cookies (sent automatically by browser)
 */
export async function authFetch(
	url: string,
	options: RequestInit = {},
): Promise<Response> {
	// Check if this is an auth endpoint that should skip token refresh
	const isAuthEndpoint = AUTH_ENDPOINTS.some((ep) => url.startsWith(ep));

	// Ensure valid token before request (skip for auth endpoints)
	if (!isAuthEndpoint) {
		const hasValidToken = await ensureValidToken();
		if (!hasValidToken) {
			handleAuthFailure();
			throw new Error("Authentication required");
		}
	}

	const headers = new Headers(options.headers);
	const method = options.method?.toUpperCase() || "GET";

	// Auth: prefer cookie (automatic), fall back to Bearer from the active
	// token store (sessionStorage embed token, or localStorage user token).
	if (!headers.has("Authorization")) {
		const token = getActiveToken();
		if (token) {
			headers.set("Authorization", `Bearer ${token}`);
		}
	}

	// Add user context
	const userId = sessionStorage.getItem("userId");
	if (userId) {
		headers.set("X-User-Id", userId);
	}

	// Add CSRF token for mutating requests
	if (requiresCsrf(method)) {
		const csrfToken = getCsrfToken();
		if (csrfToken) {
			headers.set("X-CSRF-Token", csrfToken);
		}
	}

	// Default to JSON content type for POST/PUT/PATCH
	// BUT: Don't set Content-Type if body is FormData (browser will set it with boundary)
	if (
		["POST", "PUT", "PATCH"].includes(method) &&
		!headers.has("Content-Type") &&
		!(options.body instanceof FormData)
	) {
		headers.set("Content-Type", "application/json");
	}

	// Build request for retry support
	const request = new Request(url, {
		...options,
		headers,
		credentials: "same-origin",
	});

	// Wrap the fetch + auth-handling in transient-5xx retry. Each attempt
	// clones the assembled Request so any body stream is fresh.
	return withTransient5xxRetry(method, async () => {
		const response = await fetch(request.clone());
		return handleAuthResponse(request.clone(), response);
	});
}
