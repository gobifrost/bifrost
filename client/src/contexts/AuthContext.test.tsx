import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
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

afterEach(() => {
	vi.unstubAllGlobals();
	localStorage.clear();
	sessionStorage.clear();
});

it("shares cookie recovery between provider bootstrap and an initial API request", async () => {
	const token = `header.${btoa(JSON.stringify({ sub: "account", email: "account@example.test", roles: ["authenticated"], exp: Math.floor(Date.now() / 1000) + 3600 }))}.signature`;
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
		const url = typeof input === "string" ? input : input.toString();
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
	const originalRemove = Storage.prototype.removeItem;
	const remove = vi.spyOn(Storage.prototype, "removeItem");
	let restoredBeforeRelease = false;
	remove.mockImplementation(function (this: Storage, key: string) {
		if (key === IMPERSONATION_TOKEN_KEY) restoredBeforeRelease = localStorage.getItem(ACCESS_TOKEN_KEY) === restoredToken;
		return originalRemove.call(this, key);
	});
	let end: (() => Promise<void>) | undefined;
	function Probe() { end = useAuth().endImpersonation; return null; }
	try {
		render(<MemoryRouter><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
		await vi.waitFor(() => expect(end).toBeDefined());
		await act(async () => { await end!(); });
		expect(restoredBeforeRelease).toBe(true);
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
