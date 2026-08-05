/**
 * Auth token storage utilities.
 *
 * The platform stores two distinct kinds of access token:
 *
 * 1. The user's normal access token — issued by the login flow, kept in
 *    `localStorage`, shared across all tabs of the same origin.
 *
 * 2. An embed token — minted by `/embed/apps/{slug}` (or `/embed/forms/{id}`)
 *    after a successful HMAC check, scoped to a single application or form
 *    plus a set of `verified_params`, valid for ~8 hours, with no refresh
 *    flow. Embed tokens are kept in `sessionStorage` so they stay isolated
 *    to the iframe / tab that received them and never overwrite the user's
 *    normal session in another tab on the same origin.
 *
 * The embed token is delivered via a URL fragment (`#embed_token=…`) so it
 * never reaches the server in a Referer header or proxy log.
 */

export const ACCESS_TOKEN_KEY = "bifrost_access_token";
export const EMBED_TOKEN_KEY = "bifrost_embed_token";

const EMBED_HASH_PREFIX = "#embed_token=";

export interface EmbedTokenClaims {
	embed?: boolean;
	embed_kind?: "app" | "form";
	grant?: "hmac" | "public";
	form_id?: string;
}

function parseTokenClaims(token: string): EmbedTokenClaims | null {
	try {
		const encoded = token.split(".")[1];
		if (!encoded) return null;
		const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
		const padded = normalized.padEnd(
			normalized.length + ((4 - (normalized.length % 4)) % 4),
			"=",
		);
		return JSON.parse(atob(padded)) as EmbedTokenClaims;
	} catch {
		return null;
	}
}

/**
 * Detect whether this browsing context is rendered inside an iframe.
 * A SecurityError when reading `window.top` indicates a cross-origin parent —
 * which is itself proof that we're framed.
 */
export function isInIframe(): boolean {
	try {
		return window.top !== window.self;
	} catch {
		return true;
	}
}

/**
 * Consume an `#embed_token=…` fragment from the current URL, if present,
 * and store the token in sessionStorage. Strips the fragment from the URL
 * either way so the token never lingers in the address bar or history.
 *
 * Only stores the token when running inside an iframe — opening an embed
 * URL in a regular tab would otherwise turn that tab into a sticky embed
 * session, surprising the user. The HMAC check on the server is the real
 * security boundary; the iframe guard is purely UX hygiene.
 */
export function consumeEmbedTokenFromHash(): void {
	if (typeof window === "undefined") return;
	if (!window.location.hash.startsWith(EMBED_HASH_PREFIX)) return;

	const token = window.location.hash.slice(EMBED_HASH_PREFIX.length);

	// Always strip the fragment so the token doesn't sit in the URL bar.
	window.history.replaceState(
		null,
		"",
		window.location.pathname + window.location.search,
	);

	if (token && isInIframe()) {
		sessionStorage.setItem(EMBED_TOKEN_KEY, token);
	}
}

/**
 * Return the access token to attach to outbound requests from this tab.
 * Prefers the per-tab embed token when present (we're inside an embed
 * context), otherwise falls back to the user's normal access token.
 */
export function getActiveToken(): string | null {
	const embed = sessionStorage.getItem(EMBED_TOKEN_KEY);
	if (embed) return embed;
	return localStorage.getItem(ACCESS_TOKEN_KEY);
}

/** Read non-sensitive routing claims from the tab-scoped embed token. */
export function getEmbedTokenClaims(): EmbedTokenClaims | null {
	const token = sessionStorage.getItem(EMBED_TOKEN_KEY);
	return token ? parseTokenClaims(token) : null;
}

/**
 * True when this tab is operating as an embed session (has an embed token
 * in sessionStorage). Used to skip the normal-session refresh flow, which
 * doesn't apply to embed tokens.
 */
export function isEmbedSession(): boolean {
	return sessionStorage.getItem(EMBED_TOKEN_KEY) !== null;
}

/**
 * Clear both kinds of access token. Called on logout and on hard auth
 * failures so a stale embed token can't keep an authenticated session
 * alive after the user has signed out.
 */
export function clearAuthTokens(): void {
	localStorage.removeItem(ACCESS_TOKEN_KEY);
	sessionStorage.removeItem(EMBED_TOKEN_KEY);
}

/**
 * Drop any embed token from this tab. Called from the regular login flow:
 * if a user explicitly logs in from a tab that previously held an embed
 * token, the new login should win and not be shadowed by sessionStorage.
 */
export function clearEmbedToken(): void {
	sessionStorage.removeItem(EMBED_TOKEN_KEY);
}
