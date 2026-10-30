import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext";
import { authFetch } from "@/lib/api-client";
import {
	ACCESS_TOKEN_KEY,
	IMPERSONATION_TOKEN_KEY,
	setImpersonationToken,
} from "@/lib/auth-token";

const toast = vi.hoisted(() => ({ error: vi.fn() }));

vi.mock("sonner", () => ({ toast }));

function requestUrl(input: RequestInfo | URL): string {
	if (typeof input === "string") return input;
	if (input instanceof URL) return input.href;
	return input.url;
}

function currentUser(roles: string[] = ["reader"]) {
	return {
		id: "account",
		email: "account@example.test",
		name: "Account",
		is_active: true,
		is_superuser: false,
		is_verified: true,
		organization_id: "org-1",
		roles,
	};
}

let restoreWebLocks: (() => void) | null = null;

beforeEach(() => {
	const previousLocks = Object.getOwnPropertyDescriptor(navigator, "locks");
	Object.defineProperty(navigator, "locks", {
		configurable: true,
		value: {
			request: async <T,>(_name: string, callback: () => Promise<T>) => callback(),
		},
	});
	restoreWebLocks = () => {
		if (previousLocks) Object.defineProperty(navigator, "locks", previousLocks);
		else delete (navigator as unknown as { locks?: unknown }).locks;
	};
});

afterEach(() => {
	restoreWebLocks?.();
	restoreWebLocks = null;
	vi.unstubAllGlobals();
	localStorage.clear();
	sessionStorage.clear();
});

it("loads canonical identity after cookie recovery from a compressed human token", async () => {
	const token = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "compressed-claims" }))}.signature`;
	let release!: () => void;
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	const refresh = vi.fn(async () => {
		await gate;
		return Response.json({ access_token: token });
	});
	vi.stubGlobal(
		"fetch",
		vi.fn(async (input: RequestInfo | URL) => {
			const url =
				typeof input === "string"
					? input
					: input instanceof URL
						? input.href
						: input.url;
			if (url.endsWith("/auth/status"))
				return Response.json({ needs_setup: false });
			if (url.endsWith("/api/auth/refresh")) return refresh();
			if (url.endsWith("/auth/me")) {
				return Response.json({
					id: "account",
					email: "account@example.test",
					name: "Account",
					is_active: true,
					is_superuser: false,
					is_verified: true,
					organization_id: "org-1",
					roles: ["authenticated"],
				});
			}
			return Response.json({ ok: true });
		}),
	);
	function Probe() {
		const auth = useAuth();
		return (
			<p>
				{auth.isLoading
					? "Checking session"
					: (auth.user?.email ?? "Signed out")}
			</p>
		);
	}
	render(
		<MemoryRouter>
			<AuthProvider>
				<Probe />
			</AuthProvider>
		</MemoryRouter>,
	);
	await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
	const apiRequest = authFetch("/api/profile");
	await act(async () => {
		release();
		await apiRequest;
	});
	expect(refresh).toHaveBeenCalledTimes(1);
	expect(await screen.findByText("account@example.test")).toBeVisible();
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(token);
});

it("replaces displayed roles with canonical identity after refresh", async () => {
	const initial = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "old" }))}.signature`;
	const refreshed = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "new" }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, initial);
	let useNewRoles = false;
	vi.stubGlobal(
		"fetch",
		vi.fn(async (input: RequestInfo | URL) => {
			const url = requestUrl(input);
			if (url.endsWith("/auth/status")) return Response.json({ needs_setup: false });
			if (url.endsWith("/api/auth/refresh")) return Response.json({ access_token: refreshed });
			if (url.endsWith("/auth/me")) {
				return Response.json(currentUser(useNewRoles ? ["owner"] : ["reader"]));
			}
			return Response.json({});
		}),
	);
	let refreshToken: (() => Promise<boolean>) | undefined;
	function Probe() {
		const auth = useAuth();
		refreshToken = auth.refreshToken;
		return <p>{auth.hasRole("owner") ? "Owner" : "Reader"}</p>;
	}
	render(<MemoryRouter><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
	await screen.findByText("Reader");
	useNewRoles = true;
	await act(async () => {
		await expect(refreshToken!()).resolves.toBe(true);
	});
	expect(await screen.findByText("Owner")).toBeVisible();
});

it("refreshes a stale epoch during bootstrap before loading canonical roles", async () => {
	const stale = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "stale" }))}.signature`;
	const fresh = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "fresh" }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, stale);
	let meCalls = 0;
	const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
		const url = requestUrl(input);
		if (url.endsWith("/auth/status")) return Response.json({ needs_setup: false });
		if (url.endsWith("/api/auth/refresh")) return Response.json({ access_token: fresh });
		if (url.endsWith("/auth/me")) {
			meCalls += 1;
			if (meCalls === 1) {
				return new Response(null, {
					status: 401,
					headers: {
						"WWW-Authenticate":
							'Bearer error="invalid_token", error_description="refresh_required"',
					},
				});
			}
			return Response.json(currentUser(["owner"]));
		}
		return Response.json({});
	});
	vi.stubGlobal("fetch", fetchMock);
	function Probe() {
		const auth = useAuth();
		return <p>{auth.hasRole("owner") ? "Owner" : "Reader"}</p>;
	}
	render(<MemoryRouter initialEntries={["/login"]}><AuthProvider><Probe /></AuthProvider></MemoryRouter>);

	expect(await screen.findByText("Owner")).toBeVisible();
	expect(fetchMock.mock.calls.filter(([input]) => requestUrl(input).endsWith("/api/auth/refresh"))).toHaveLength(1);
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(fresh);
});

it("leaves an ended bootstrap session signed out", async () => {
	const stale = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "stale" }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, stale);
	vi.stubGlobal(
		"fetch",
		vi.fn(async (input: RequestInfo | URL) => {
			const url = requestUrl(input);
			if (url.endsWith("/auth/status")) return Response.json({ needs_setup: false });
			if (url.endsWith("/auth/me")) {
				return new Response(null, {
					status: 401,
					headers: {
						"WWW-Authenticate":
							'Bearer error="invalid_token", error_description="refresh_required"',
					},
				});
			}
			if (url.endsWith("/api/auth/refresh")) return new Response(null, { status: 401 });
			return Response.json({});
		}),
	);
	function Probe() {
		const auth = useAuth();
		return <p>{auth.user?.email ?? "Signed out"}</p>;
	}
	render(<MemoryRouter initialEntries={["/login"]}><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
	expect(await screen.findByText("Signed out")).toBeVisible();
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
});

it("does not restore identity after logout wins an in-flight identity read", async () => {
	const token = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "current" }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, token);
	let completeIdentity!: (response: Response) => void;
	const identity = new Promise<Response>((resolve) => {
		completeIdentity = resolve;
	});
	vi.stubGlobal(
		"fetch",
		vi.fn(async (input: RequestInfo | URL) => {
			const url = requestUrl(input);
			if (url.endsWith("/auth/status")) return Response.json({ needs_setup: false });
			if (url.endsWith("/auth/me")) return identity;
			if (url.endsWith("/api/auth/logout")) return new Response(null, { status: 204 });
			return Response.json({});
		}),
	);
	let logout: (() => void) | undefined;
	function Probe() {
		const auth = useAuth();
		logout = auth.logout;
		return <p>{auth.user?.email ?? "Signed out"}</p>;
	}
	render(<MemoryRouter initialEntries={["/login"]}><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
	await vi.waitFor(() => expect(logout).toBeDefined());
	act(() => logout!());
	await vi.waitFor(() => expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull());
	await act(async () => {
		completeIdentity(Response.json(currentUser()));
		await identity;
	});
	expect(await screen.findByText("Signed out")).toBeVisible();
});

it("rotates a stale normal session before completing logout once", async () => {
	const stale = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "stale" }))}.signature`;
	const fresh = `header.${btoa(JSON.stringify({ sub: "account", exp: Math.floor(Date.now() / 1000) + 3600, usr: "fresh" }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, stale);
	let logoutRequests = 0;
	let completedLogouts = 0;
	const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
		const url = requestUrl(input);
		if (url.endsWith("/auth/status")) return Response.json({ needs_setup: false });
		if (url.endsWith("/auth/me")) return Response.json(currentUser());
		if (url.endsWith("/api/auth/refresh")) {
			return Response.json({ access_token: fresh });
		}
		if (url.endsWith("/api/auth/logout")) {
			logoutRequests += 1;
			if (logoutRequests === 1) return new Response(null, {
				status: 401,
				headers: {
					"WWW-Authenticate": 'Bearer error="invalid_token", error_description="refresh_required"',
				},
			});
			completedLogouts += 1;
			return new Response(null, { status: 204 });
		}
		return Response.json({});
	});
	vi.stubGlobal("fetch", fetchMock);
	let logout: (() => void) | undefined;
	function Probe() {
		const auth = useAuth();
		logout = auth.logout;
		return <p>{auth.user?.email ?? "Signed out"}</p>;
	}
	render(<MemoryRouter initialEntries={["/login"]}><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
	await screen.findByText("account@example.test");

	act(() => logout!());
	await vi.waitFor(() => expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull());

	expect(logoutRequests).toBe(2);
	expect(completedLogouts).toBe(1);
	expect(fetchMock.mock.calls.filter(([input]) => requestUrl(input).endsWith("/api/auth/refresh"))).toHaveLength(1);
});

it("reloads an existing normal tab when impersonation or logout changes shared auth", async () => {
	const reload = vi.fn();
	const originalLocation = window.location;
	Object.defineProperty(window, "location", {
		configurable: true,
		value: { ...originalLocation, reload },
	});
	vi.stubGlobal(
		"fetch",
		vi.fn(async () => Response.json({ needs_setup: false })),
	);

	try {
		render(
			<MemoryRouter>
				<AuthProvider>
					<p>Normal tab</p>
				</AuthProvider>
			</MemoryRouter>,
		);
		await screen.findByText("Normal tab");

		window.dispatchEvent(
			new StorageEvent("storage", {
				key: IMPERSONATION_TOKEN_KEY,
				newValue: "impersonated-token",
				storageArea: localStorage,
			}),
		);

		window.dispatchEvent(
			new StorageEvent("storage", {
				key: ACCESS_TOKEN_KEY,
				newValue: null,
				storageArea: localStorage,
			}),
		);

		expect(reload).toHaveBeenCalledTimes(2);
	} finally {
		Object.defineProperty(window, "location", {
			configurable: true,
			value: originalLocation,
		});
	}
});

it.each(["end", "refresh"])("keeps the override when operator restoration fails at %s", async (failure) => {
	const operator = "operator-token";
	localStorage.setItem(ACCESS_TOKEN_KEY, operator);
	setImpersonationToken("impersonated-token");
	const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
		const url = typeof input === "string" ? input : input.toString();
		if (url.endsWith("/auth/status")) return Response.json({ needs_setup: false });
		if (url.endsWith("/api/auth/impersonation/end")) {
			return new Response(null, { status: failure === "end" ? 500 : 204 });
		}
		if (url.endsWith("/api/auth/refresh")) return new Response(null, { status: 500 });
		return Response.json({});
	});
	vi.stubGlobal("fetch", fetchMock);
	let endImpersonation: (() => Promise<void>) | undefined;
	function Probe() {
		endImpersonation = useAuth().endImpersonation;
		return null;
	}
	render(
		<MemoryRouter>
			<AuthProvider>
				<Probe />
			</AuthProvider>
		</MemoryRouter>,
	);
	await vi.waitFor(() => expect(endImpersonation).toBeDefined());

	await expect(endImpersonation!()).rejects.toThrow();
	const [, init] = fetchMock.mock.calls.find(([input]) =>
		String(input).endsWith("/api/auth/impersonation/end"),
	)!;
	expect(new Headers(init?.headers).get("Authorization")).toBe(
		`Bearer ${operator}`,
	);
	expect(localStorage.getItem(IMPERSONATION_TOKEN_KEY)).toBe(
		"impersonated-token",
	);
});

it("keeps the browser-wide support session when logout cannot revoke it", async () => {
	localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");
	setImpersonationToken("impersonated-token");
	const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
		const url = requestUrl(input);
		if (url.endsWith("/auth/status")) return Response.json({ needs_setup: false });
		if (url.endsWith("/auth/logout")) return new Response(null, { status: 500 });
		return Response.json({});
	});
	vi.stubGlobal("fetch", fetchMock);
	let logout: (() => void) | undefined;
	function Probe() {
		logout = useAuth().logout;
		return null;
	}
	render(
		<MemoryRouter>
			<AuthProvider>
				<Probe />
			</AuthProvider>
		</MemoryRouter>,
	);
	await vi.waitFor(() => expect(logout).toBeDefined());

	act(() => logout!());
	await vi.waitFor(() => expect(toast.error).toHaveBeenCalledOnce());

	expect(fetchMock).toHaveBeenCalledWith(
		"/api/auth/logout",
		expect.objectContaining({
			method: "POST",
			headers: { Authorization: "Bearer impersonated-token" },
		}),
	);
	expect(localStorage.getItem(IMPERSONATION_TOKEN_KEY)).toBe(
		"impersonated-token",
	);
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe("operator-token");
});

it("signs an embed out without clearing shared cookies or browser credentials", async () => {
	localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");
	setImpersonationToken("impersonated-token");
	sessionStorage.setItem("bifrost_embed_token", "embed-token");
	const fetchMock = vi.fn(async (_url: RequestInfo | URL) => Response.json({ needs_setup: false }));
	vi.stubGlobal("fetch", fetchMock);
	let logout: (() => void) | undefined;
	function Probe() {
		logout = useAuth().logout;
		return null;
	}
	render(<MemoryRouter><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
	await vi.waitFor(() => expect(logout).toBeDefined());
	act(() => logout!());
	expect(sessionStorage.getItem("bifrost_embed_token")).toBeNull();
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe("operator-token");
	expect(localStorage.getItem(IMPERSONATION_TOKEN_KEY)).toBe("impersonated-token");
	expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/auth/logout"))).toBe(false);
});

it("restores the operator token before releasing the override to other tabs", async () => {
	const restoredToken = `header.${btoa(JSON.stringify({ sub: "operator", email: "operator@example.test", exp: Math.floor(Date.now() / 1000) + 1800 }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, "expired-operator-token");
	setImpersonationToken("impersonated-token");
	const assign = vi.fn();
	const originalLocation = window.location;
	Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, assign } });
	const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
		const url = String(input);
		if (url.endsWith("/api/auth/impersonation/end")) return new Response(null, { status: 204 });
		if (url.endsWith("/api/auth/refresh")) {
			expect(localStorage.getItem(IMPERSONATION_TOKEN_KEY)).toBe("impersonated-token");
			return Response.json({ access_token: restoredToken });
		}
		return Response.json({ needs_setup: false });
	});
	vi.stubGlobal("fetch", fetchMock);
	const originalRemove = localStorage.removeItem.bind(localStorage);
	const remove = vi.spyOn(localStorage, "removeItem");
	const markerRemovalTokens: Array<string | null> = [];
	remove.mockImplementation(function (this: Storage, key: string) {
		if (key === IMPERSONATION_TOKEN_KEY) {
			markerRemovalTokens.push(localStorage.getItem(ACCESS_TOKEN_KEY));
		}
		return originalRemove(key);
	});
	let end: (() => Promise<void>) | undefined;
	function Probe() { end = useAuth().endImpersonation; return null; }
	try {
		render(<MemoryRouter><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
		await vi.waitFor(() => expect(end).toBeDefined());
		await act(async () => { await end!(); });
		expect(markerRemovalTokens).toEqual([restoredToken]);
		expect(localStorage.getItem(IMPERSONATION_TOKEN_KEY)).toBeNull();
		expect(assign).toHaveBeenCalledWith("/users");
	} finally {
		remove.mockRestore();
		Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
	}
});

it("preserves a CLI code and fragment through the shared sign-in redirect", async () => {
	vi.stubGlobal(
		"fetch",
		vi.fn(async (input: RequestInfo | URL) => {
			const url = typeof input === "string" ? input : input.toString();
			if (url.endsWith("/auth/status"))
				return Response.json({ needs_setup: false });
			return new Response(null, { status: 401 });
		}),
	);
	function LocationProbe() {
		const location = useLocation();
		return (
			<p>
				{location.pathname} {JSON.stringify(location.state)}
			</p>
		);
	}
	render(
		<MemoryRouter initialEntries={["/device?user_code=ABCD-EFGH#connect"]}>
			<AuthProvider>
				<LocationProbe />
			</AuthProvider>
		</MemoryRouter>,
	);
	expect(
		await screen.findByText(
			'/login {"from":"/device?user_code=ABCD-EFGH#connect"}',
		),
	).toBeVisible();
});
