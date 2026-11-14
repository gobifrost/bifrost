/**
 * Turn a post-login redirect target into a same-origin path for navigate().
 *
 * MCP OAuth (and older servers) send an absolute `return_to` such as
 * `https://host/mcp/callback?internal_state=...`. React Router treats any
 * string that does not start with "/" as relative to the current route, so
 * navigating to it from /auth/callback/<provider> produced a 404 like
 * /auth/callback/microsoft/https:/host/mcp/callback.
 *
 * Same-origin targets become `pathname + search + hash`. Anything that
 * resolves to another origin (absolute URLs, `//host`, `javascript:`) or
 * cannot be parsed falls back to "/" so `from` is never an open redirect.
 */
export function toSameOriginPath(
	target: string | null | undefined,
	origin: string = window.location.origin,
): string {
	if (!target) return "/";
	try {
		const url = new URL(target, origin);
		if (url.origin !== origin) return "/";
		return url.pathname + url.search + url.hash;
	} catch {
		return "/";
	}
}
