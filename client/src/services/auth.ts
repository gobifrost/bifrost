/**
 * Auth Service
 *
 * API methods for authentication, MFA, and OAuth operations.
 * Uses auto-generated types from OpenAPI spec.
 */

import type { components } from "@/lib/v1";

// =============================================================================
// Types - Auto-generated from OpenAPI spec
// =============================================================================

export type OAuthProvider = components["schemas"]["src__models__contracts__auth__OAuthProviderInfo"];
export type AuthStatus = components["schemas"]["AuthStatusResponse"];
export type OAuthInitResponse = components["schemas"]["OAuthInitResponse"];
export type MFAStatus = components["schemas"]["MFAStatusResponse"];
export type TOTPSetupResponse = components["schemas"]["src__routers__auth__MFASetupResponse"];
export type TOTPVerifyResponse = components["schemas"]["MFAVerifyResponse"];
export type RecoveryCodesCount =
	components["schemas"]["RecoveryCodesCountResponse"];

// =============================================================================
// Auth Status
// =============================================================================

export async function getAuthStatus(): Promise<AuthStatus> {
	const res = await fetch("/auth/status");
	if (!res.ok) throw new Error("Failed to get auth status");
	return res.json();
}

// =============================================================================
// OAuth Providers
// =============================================================================

/**
 * Get available OAuth providers.
 * Uses the auth status endpoint which includes provider info.
 */
export async function getOAuthProviders(): Promise<OAuthProvider[]> {
	const status = await getAuthStatus();
	return status.oauth_providers;
}

export async function initOAuth(
	provider: string,
	redirectUri: string,
): Promise<OAuthInitResponse> {
	const res = await fetch(
		`/auth/oauth/init/${provider}?redirect_uri=${encodeURIComponent(redirectUri)}`,
	);
	if (!res.ok) throw new Error("Failed to initialize OAuth");
	return res.json();
}

// Note: getOAuthVerifier() has been removed - PKCE is now handled server-side
// The backend stores the code_verifier when init is called and uses it during callback

/**
 * Hash the OAuth `state` (a CSRF nonce) before persisting it for the callback
 * round-trip. We store only the SHA-256 digest, never the raw token: the
 * browser-binding CSRF check still works by comparing digests, but an XSS
 * reader of sessionStorage can't lift a usable state value. SubtleCrypto is
 * available in the secure (HTTPS/localhost) contexts where OAuth runs.
 */
export async function hashOAuthState(state: string): Promise<string> {
	const digest = await crypto.subtle.digest(
		"SHA-256",
		new TextEncoder().encode(state),
	);
	return Array.from(new Uint8Array(digest))
		.map((b) => b.toString(16).padStart(2, "0"))
		.join("");
}

// =============================================================================
// MFA Operations
// =============================================================================

export async function getMFAStatus(): Promise<MFAStatus> {
	const res = await fetch("/auth/mfa/status");
	if (!res.ok) throw new Error("Failed to get MFA status");
	return res.json();
}

export async function setupTOTP(): Promise<TOTPSetupResponse> {
	const res = await fetch("/auth/mfa/setup", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
	});
	if (!res.ok) throw new Error("Failed to setup TOTP");
	return res.json();
}

export async function verifyTOTPSetup(
	code: string,
): Promise<TOTPVerifyResponse> {
	const res = await fetch("/auth/mfa/verify", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ code }),
	});
	if (!res.ok) throw new Error("Failed to verify TOTP");
	return res.json();
}

export async function removeTOTP(
	password?: string,
	mfaCode?: string,
): Promise<void> {
	const res = await fetch("/auth/mfa", {
		method: "DELETE",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ password, mfa_code: mfaCode }),
	});
	if (!res.ok) throw new Error("Failed to remove TOTP");
}

export async function getRecoveryCodesCount(): Promise<RecoveryCodesCount> {
	const res = await fetch("/auth/mfa/recovery-codes/count");
	if (!res.ok) throw new Error("Failed to get recovery codes count");
	return res.json();
}

export async function regenerateRecoveryCodes(
	mfaCode: string,
): Promise<string[]> {
	const res = await fetch("/auth/mfa/recovery-codes/regenerate", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ mfa_code: mfaCode }),
	});
	if (!res.ok) throw new Error("Failed to regenerate recovery codes");
	const data = await res.json();
	return data.recovery_codes;
}

// =============================================================================
// Trusted Devices
// =============================================================================
// Note: Trusted devices API endpoints not yet implemented
// TrustedDevice ORM model exists but no API routes exposed yet

// =============================================================================
// User Registration (for setup)
// =============================================================================

export async function registerUser(
	email: string,
	password: string,
	name?: string,
): Promise<void> {
	const res = await fetch("/auth/register", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ email, password, name }),
	});

	if (!res.ok) {
		const error = await res.json().catch(() => ({}));
		throw new Error(error.detail || "Registration failed");
	}
}

/**
 * Complete an invite by setting a password. Uses a raw fetch (not apiClient)
 * because the invitee is unauthenticated — the invite token IS the credential.
 * Routing this through apiClient would trip its token-refresh middleware, which
 * redirects unauthenticated callers to /login before the request is ever sent.
 */
export async function registerFromInvite(
	token: string,
	password: string,
): Promise<void> {
	const res = await fetch("/auth/register-from-invite", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ token, password }),
	});

	if (!res.ok) {
		const error = await res.json().catch(() => ({}));
		throw new Error(error.detail || "Registration failed");
	}
}
