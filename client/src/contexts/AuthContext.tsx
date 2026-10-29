/**
 * Authentication Context
 *
 * Provides JWT-based authentication state throughout the application.
 * Handles token storage, refresh, and user state management.
 */

import {
	createContext,
	useContext,
	useCallback,
	useEffect,
	useMemo,
	useState,
	type ReactNode,
} from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import {
	ACCESS_TOKEN_KEY,
	IMPERSONATION_TOKEN_KEY,
	clearAuthTokens,
	clearEmbedToken,
	clearImpersonationToken,
	consumeEmbedTokenFromHash,
	getActiveToken,
	isEmbedSession,
	isImpersonating,
	setImpersonationToken,
} from "@/lib/auth-token";
import { clearPreferredSsoRedirectAttempt } from "@/services/auth";
import { refreshAccessToken } from "@/lib/api-client";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

// Consume the fragment before AuthProvider performs its initial auth check.
// api-client does the same defensively before requests; this keeps auth state
// correct even on a route whose first render has not imported an API service.
consumeEmbedTokenFromHash();

// User info extracted from JWT
export interface AuthUser {
	id: string;
	email: string;
	name: string;
	userType: "PLATFORM" | "ORG";
	isSuperuser: boolean;
	organizationId: string | null;
	roles: string[];
}

// Login response with MFA state
export interface LoginResult {
	success: boolean;
	mfaRequired?: boolean;
	mfaSetupRequired?: boolean;
	mfaToken?: string;
	availableMethods?: string[];
	expiresIn?: number;
}

export interface ImpersonationTarget {
	id: string;
	email: string;
	name: string;
}

interface AuthContextValue {
	// State
	user: AuthUser | null;
	isAuthenticated: boolean;
	isLoading: boolean;
	needsSetup: boolean;

	// Role helpers
	isPlatformAdmin: boolean;
	isOrgUser: boolean;
	hasRole: (role: string) => boolean;
	isImpersonating: boolean;

	// Actions
	login: (email: string, password: string) => Promise<LoginResult>;
	loginWithMfa: (
		mfaToken: string,
		code: string,
		trustDevice?: boolean,
	) => Promise<void>;
	loginWithOAuth: (
		provider: string,
		code: string,
		state: string,
	) => Promise<void>;
	loginWithPasskey: (email?: string) => Promise<void>;
	completeLoginWithToken: (accessToken: string) => void;
	logout: () => void;
	refreshToken: () => Promise<boolean>;
	checkAuthStatus: () => Promise<void>;
	startImpersonation: (target: ImpersonationTarget) => Promise<void>;
	endImpersonation: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// Token storage keys
// Note: Refresh token is stored in HttpOnly cookie only (more secure)
const USER_KEY = "bifrost_user";

// JWT payload structure
interface JwtPayload {
	sub?: string;
	email?: string;
	name?: string;
	is_superuser?: boolean;
	org_id?: string | null;
	roles?: string[];
	exp?: number;
}

// Parse JWT payload (without verification - server validates)
function parseJwt(token: string): JwtPayload | null {
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

// Extract user from JWT payload
function extractUser(payload: JwtPayload): AuthUser {
	const isSuperuser = payload.is_superuser || false;
	const organizationId = payload.org_id || null;
	return {
		id: payload.sub || "",
		email: payload.email || "",
		name: payload.name || "",
		// Derive userType from is_superuser: platform users are superusers
		userType: isSuperuser ? "PLATFORM" : "ORG",
		isSuperuser,
		organizationId,
		roles: payload.roles || [],
	};
}

// Check if token is expired (with 30s buffer)
function isTokenExpired(token: string): boolean {
	const payload = parseJwt(token);
	if (!payload || payload.exp === undefined) return true;
	return Date.now() >= (payload.exp - 30) * 1000;
}

interface AuthProviderProps {
	children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
	const [user, setUser] = useState<AuthUser | null>(null);
	const [isLoading, setIsLoading] = useState(true);
	const [needsSetup, setNeedsSetup] = useState(false);
	const navigate = useNavigate();
	const location = useLocation();

	const completeLoginWithToken = useCallback((accessToken: string): void => {
		clearEmbedToken();
		clearImpersonationToken();
		clearPreferredSsoRedirectAttempt();
		localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);

		const payload = parseJwt(accessToken);
		if (payload) {
			const extractedUser = extractUser(payload);
			setUser(extractedUser);
			localStorage.setItem(USER_KEY, JSON.stringify(extractedUser));
			sessionStorage.setItem("userId", extractedUser.id);
		}
	}, []);

	// Internal refresh function (no hooks)
	// Uses HttpOnly cookie for refresh token (browser sends it automatically)
	const refreshTokenInternal = useCallback(async (): Promise<boolean> => {
		if (isImpersonating()) return false;
		try {
			// Share the API/SDK refresh lock: rotating the same refresh cookie twice
			// during bootstrap can invalidate an otherwise valid browser session.
			if (!(await refreshAccessToken())) return false;
			const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY);
			if (accessToken) {
				const payload = parseJwt(accessToken);
				if (payload) {
					const extractedUser = extractUser(payload);
					setUser(extractedUser);
					localStorage.setItem(
						USER_KEY,
						JSON.stringify(extractedUser),
					);
					sessionStorage.setItem("userId", extractedUser.id);
				}
				return true;
			}
			return false;
		} catch {
			return false;
		}
	}, []);

	// Check auth status on mount
	const checkAuthStatus = useCallback(async () => {
		try {
			// Check if system needs initial setup
			const statusRes = await fetch("/auth/status");
			if (statusRes.ok) {
				const status = await statusRes.json();
				setNeedsSetup(status.needs_setup);

				if (status.needs_setup) {
					setIsLoading(false);
					return;
				}
			}

			// Check for existing token
			// Note: embed tokens from URL fragments are extracted at module load
			// time in api-client.ts, before any API calls or auth checks run.
			let token = getActiveToken();
			if (!token) {
				// No access token in localStorage, but refresh_token cookie may still be valid
				const refreshed = await refreshTokenInternal();
				if (!refreshed) {
					setUser(null);
					setIsLoading(false);
					return;
				}
				token = localStorage.getItem(ACCESS_TOKEN_KEY);
				if (!token) {
					setUser(null);
					setIsLoading(false);
					return;
				}
			}

			// Check if token is expired
			if (isTokenExpired(token)) {
				if (isEmbedSession()) {
					clearEmbedToken();
					setUser(null);
					setIsLoading(false);
					return;
				}
				// Impersonation has no refresh cookie. Keep its session in place so
				// the explicit Exit action can return to the saved operator.
				if (!isImpersonating()) {
					// Try to refresh
					const refreshed = await refreshTokenInternal();
					if (!refreshed) {
						setUser(null);
						setIsLoading(false);
						return;
					}
				}
			}

			// Parse user from token (re-read in case it was refreshed)
			const currentToken = getActiveToken();
			const payload = parseJwt(currentToken || token);
			if (payload) {
				const extractedUser = extractUser(payload);
				setUser(extractedUser);

				// Store user ID for org context
				sessionStorage.setItem("userId", extractedUser.id);
			}
		} catch (error) {
			console.error("Auth check failed:", error);
			setUser(null);
		} finally {
			setIsLoading(false);
		}
	}, [refreshTokenInternal]);

	// Login with email/password
	const login = useCallback(
		async (email: string, password: string): Promise<LoginResult> => {
			const formData = new FormData();
			formData.append("username", email);
			formData.append("password", password);

			const res = await fetch("/auth/login", {
				method: "POST",
				body: formData,
			});

			if (!res.ok) {
				const error = await res.json().catch(() => ({}));
				throw new Error(error.detail || "Login failed");
			}

			const data = await res.json();

			// Check for MFA requirements
			if (data.mfa_required) {
				return {
					success: false,
					mfaRequired: true,
					mfaToken: data.mfa_token,
					availableMethods: data.available_methods,
					expiresIn: data.expires_in,
				};
			}

			if (data.mfa_setup_required) {
				return {
					success: false,
					mfaSetupRequired: true,
					mfaToken: data.mfa_token,
					expiresIn: data.expires_in,
				};
			}

			// Success - store access token (refresh token is in HttpOnly cookie)
			if (data.access_token) {
				completeLoginWithToken(data.access_token);
				return { success: true };
			}

			throw new Error("No access token received");
		},
		[completeLoginWithToken],
	);

	// Complete MFA verification during login (for users with MFA already set up)
	const loginWithMfa = useCallback(
		async (
			mfaToken: string,
			code: string,
			trustDevice: boolean = false,
		): Promise<void> => {
			const res = await fetch("/auth/mfa/login", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					mfa_token: mfaToken,
					code,
					trust_device: trustDevice,
				}),
			});

			if (!res.ok) {
				const error = await res.json().catch(() => ({}));
				throw new Error(error.detail || "MFA verification failed");
			}

			const data = await res.json();

			if (data.access_token) {
				completeLoginWithToken(data.access_token);
			}
		},
		[completeLoginWithToken],
	);

	// Complete OAuth login
	// Note: code_verifier is now handled server-side (stored in Redis keyed by state)
	const loginWithOAuth = useCallback(
		async (
			provider: string,
			code: string,
			state: string,
		): Promise<void> => {
			const res = await fetch("/auth/oauth/callback", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					provider,
					code,
					state,
					// code_verifier not needed - server retrieves it from Redis using state
				}),
			});

			if (!res.ok) {
				const error = await res.json().catch(() => ({}));
				throw new Error(error.detail || "OAuth login failed");
			}

			const data = await res.json();

			if (data.access_token) {
				completeLoginWithToken(data.access_token);
			}
		},
		[completeLoginWithToken],
	);

	// Login with passkey (passwordless authentication)
	const loginWithPasskey = useCallback(
		async (email?: string): Promise<void> => {
			// Import dynamically to avoid bundling WebAuthn code for browsers that don't support it
			const { authenticateWithPasskey } =
				await import("@/services/passkeys");

			const tokens = await authenticateWithPasskey(email);

			if (tokens.access_token) {
				completeLoginWithToken(tokens.access_token);
			}
		},
		[completeLoginWithToken],
	);

	// Logout
	const logout = useCallback(() => {
		if (isEmbedSession()) {
			clearEmbedToken();
			setUser(null);
			navigate("/login");
			return;
		}
		void (async () => {
			try {
				const token = getActiveToken();
				const response = await fetch("/api/auth/logout", {
					method: "POST",
					headers: token ? { Authorization: `Bearer ${token}` } : undefined,
					credentials: "same-origin",
				});
				if (!response.ok) {
					const body = await response.json().catch(() => ({}));
					throw new Error(body.detail || "Could not sign out");
				}
			} catch (error) {
				toast.error("Could not sign out", {
					description: getErrorMessage(
						error,
						"The browser session could not be ended.",
					),
				});
				return;
			}

			clearAuthTokens();
			localStorage.removeItem(USER_KEY);
			sessionStorage.removeItem("userId");
			setUser(null);
			navigate("/login");
		})();
	}, [navigate]);

	// A support session changes the identity behind every regular browser tab.
	// Reload instead of trying to reconcile query caches, sockets, and mounted
	// Solution apps from the previous identity. Embed capability sessions stay
	// isolated and retain their per-tab token.
	useEffect(() => {
		const handleStorage = (event: StorageEvent) => {
			if (event.storageArea !== localStorage || isEmbedSession()) return;
			if (
				event.key === IMPERSONATION_TOKEN_KEY ||
				(event.key === ACCESS_TOKEN_KEY && event.newValue === null)
			) {
				window.location.reload();
			}
		};
		window.addEventListener("storage", handleStorage);
		return () => window.removeEventListener("storage", handleStorage);
	}, []);

	const startImpersonation = useCallback(
		async (target: ImpersonationTarget): Promise<void> => {
			if (isImpersonating()) {
				throw new Error("End the current impersonation session first");
			}
			const token = getActiveToken();
			if (!token) throw new Error("Your session has expired");
			const response = await fetch(`/api/auth/impersonate/${target.id}`, {
				method: "POST",
				headers: { Authorization: `Bearer ${token}` },
				credentials: "same-origin",
			});
			if (!response.ok) {
				const body = await response.json().catch(() => ({}));
				throw new Error(body.detail || "Could not start impersonation");
			}
			const body =
				(await response.json()) as components["schemas"]["BrowserImpersonationTokenResponse"];
			if (!body.access_token) {
				throw new Error("Impersonation did not return an access token");
			}
			setImpersonationToken(body.access_token);
			window.location.assign("/apps");
		},
		[],
	);

	const endImpersonation = useCallback(async (): Promise<void> => {
		const operator = localStorage.getItem(ACCESS_TOKEN_KEY);
		if (!operator) throw new Error("Your operator session has expired");
		const response = await fetch("/api/auth/impersonation/end", {
			method: "POST",
			headers: { Authorization: `Bearer ${operator}` },
			credentials: "same-origin",
		});
		if (!response.ok) {
			const body = await response.json().catch(() => ({}));
			throw new Error(body.detail || "Could not end impersonation");
		}

		// Restore one fresh operator token before the storage event reloads the
		// other tabs. Otherwise an expired operator token makes every tab race
		// to rotate the same single-use refresh cookie independently.
		const restored = await fetch("/api/auth/refresh", {
			method: "POST",
			credentials: "same-origin",
		});
		if (!restored.ok) throw new Error("Could not restore your operator session");
		const tokens = (await restored.json()) as components["schemas"]["Token"];
		if (!tokens.access_token) throw new Error("Operator session did not return an access token");
		localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
		clearImpersonationToken();
		const payload = parseJwt(tokens.access_token);
		if (payload) sessionStorage.setItem("userId", extractUser(payload).id);
		window.location.assign("/users");
	}, []);

	// Refresh token
	const refreshToken = useCallback(async (): Promise<boolean> => {
		return refreshTokenInternal();
	}, [refreshTokenInternal]);

	// Check auth on mount. setState only fires after the awaited fetch
	// resolves — wrapping in a void IIFE keeps the synchronous body free of
	// setState calls.
	useEffect(() => {
		void (async () => {
			await checkAuthStatus();
		})();
	}, [checkAuthStatus]);

	// Redirect unauthenticated users to login
	useEffect(() => {
		if (isLoading) return;

		// Public routes that don't require auth
		const publicRoutes = [
			"/login",
			"/setup",
			"/accept-invite",
			"/auth/callback",
			"/mfa-setup",
		];
		const isPublicRoute = publicRoutes.some((route) =>
			location.pathname.startsWith(route),
		);

		if (needsSetup && location.pathname !== "/setup") {
			navigate("/setup");
			return;
		}

		if (!user && !isPublicRoute && !needsSetup) {
			navigate("/login", {
				state: {
					from: `${location.pathname}${location.search}${location.hash}`,
				},
			});
		}
	}, [
		isLoading,
		user,
		needsSetup,
		location.pathname,
		location.search,
		location.hash,
		navigate,
	]);

	const value: AuthContextValue = useMemo(
		() => ({
			user,
			isAuthenticated: !!user,
			isLoading,
			needsSetup,
			isPlatformAdmin: user?.isSuperuser ?? false,
			isOrgUser: !user?.isSuperuser && user?.organizationId != null,
			hasRole: (role: string) => user?.roles.includes(role) ?? false,
			isImpersonating: isImpersonating(),
			login,
			loginWithMfa,
			loginWithOAuth,
			loginWithPasskey,
			completeLoginWithToken,
			logout,
			refreshToken,
			checkAuthStatus,
			startImpersonation,
			endImpersonation,
		}),
		[
			user,
			isLoading,
			needsSetup,
			login,
			loginWithMfa,
			loginWithOAuth,
			loginWithPasskey,
			completeLoginWithToken,
			logout,
			refreshToken,
			checkAuthStatus,
			startImpersonation,
			endImpersonation,
		],
	);

	return (
		<AuthContext.Provider value={value}>{children}</AuthContext.Provider>
	);
}

export function useAuth() {
	const context = useContext(AuthContext);
	if (!context) {
		throw new Error("useAuth must be used within an AuthProvider");
	}
	return context;
}

// Export token getter for API client
export function getAccessToken(): string | null {
	return getActiveToken();
}
