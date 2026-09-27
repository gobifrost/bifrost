import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	localStorage.clear();
	sessionStorage.clear();
	window.history.replaceState(null, "", "/");
});

it("starts one login navigation for concurrent and late authentication failures", async () => {
	vi.resetModules();
	window.history.replaceState(null, "", "/event-sources");
	const redirect = vi.spyOn(window.location, "href", "set").mockImplementation(() => {});
	vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 401 })));
	const { apiClient, authFetch, withUserContext } = await import("./api-client");
	const results = await Promise.allSettled([
		authFetch("/api/version"),
		apiClient.GET("/api/version"),
		withUserContext("user").GET("/api/version"),
	]);
	expect(results.map((result) => result.status)).toEqual(["rejected", "rejected", "rejected"]);
	await expect(authFetch("/api/version")).rejects.toThrow("Authentication required");
	expect(redirect).toHaveBeenCalledTimes(1);
	expect(redirect).toHaveBeenCalledWith("/login?returnTo=%2Fevent-sources");
});
