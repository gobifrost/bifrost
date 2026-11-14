/**
 * Login Page
 *
 * Handles email/password login with MFA flow, OAuth, and passkey options.
 */

import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import {
	getAuthStatus,
	hashOAuthState,
	initOAuth,
	PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY,
} from "@/services/auth";
import { supportsPasskeys } from "@/services/passkeys";
import { toSameOriginPath } from "@/lib/same-origin-path";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
	Loader2,
	KeyRound,
	Mail,
	Lock,
	ExternalLink,
	Fingerprint,
} from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import type { OAuthProvider } from "@/services/auth";
import { Logo } from "@/components/branding/Logo";
import { AuthTransition } from "@/components/auth/AuthTransition";
import {
	GoogleBrandIcon,
	MicrosoftBrandIcon,
} from "@/components/auth/ProviderBrandIcons";
import { useApplicationName } from "@/lib/applicationName";

type LoginStep = "credentials" | "mfa" | "mfa-setup";

interface MFAState {
	mfaToken: string;
	availableMethods: string[];
	expiresIn: number;
}

export function Login() {
	const reducedMotion = useReducedMotion();
	const navigate = useNavigate();
	const location = useLocation();
	const applicationName = useApplicationName();
	const accountMessage = (location.state as { message?: string } | null)
		?.message;
	const {
		login,
		loginWithMfa,
		loginWithPasskey,
		isAuthenticated,
		isLoading: authLoading,
	} = useAuth();

	const [step, setStep] = useState<LoginStep>("credentials");
	const [mfaState, setMfaState] = useState<MFAState | null>(null);

	// Form state
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [mfaCode, setMfaCode] = useState("");
	const [trustDevice, setTrustDevice] = useState(false);

	// UI state
	const [isLoading, setIsLoading] = useState(false);
	const [isPasskeyLoading, setIsPasskeyLoading] = useState(false);
	const [finalizing, setFinalizing] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [oauthProviders, setOAuthProviders] = useState<OAuthProvider[]>([]);
	const [authStatusLoaded, setAuthStatusLoaded] = useState(false);
	const [autoRedirectToSso, setAutoRedirectToSso] = useState(false);
	const [passwordLoginEnabled, setPasswordLoginEnabled] = useState(true);
	const [passkeyLoginEnabled, setPasskeyLoginEnabled] = useState(true);
	const [defaultSsoProvider, setDefaultSsoProvider] = useState<string | null>(
		null,
	);
	// Derived synchronously — `supportsPasskeys()` is a pure feature check on
	// `window` that returns the same value across renders for the lifetime of
	// the page, so it does not need to live in state.
	const passkeySupported = useMemo(() => supportsPasskeys(), []);
	// Under SSO-only enforcement the server refuses these methods for
	// non-admins; the page de-emphasizes them but stays usable for admins.
	const passwordHiddenByPolicy = !passwordLoginEnabled;
	const passkeyHiddenByPolicy = passkeySupported && !passkeyLoginEnabled;
	const showAdminExpander = passwordHiddenByPolicy || passkeyHiddenByPolicy;
	const passkeyAllowed = passkeySupported && passkeyLoginEnabled;

	// Redirect path from URL query params (for MCP OAuth) or location state
	const searchParams = new URLSearchParams(location.search);
	const returnTo =
		searchParams.get("returnTo") || searchParams.get("return_to");
	const from = returnTo || (location.state as { from?: string })?.from || "/";

	// `from` can be an absolute URL (MCP OAuth's return_to). react-router's
	// navigate() treats those as relative paths and produces nonsense like
	// /login/https:/example.com/mcp/callback, so normalize to a same-origin
	// path first so the /connect-app SPA route can resume MCP consent.
	// Cross-origin targets fall back to "/" — never
	// honor a foreign origin, it's an open-redirect vector.
	const redirectToFrom = useCallback(() => {
		navigate(toSameOriginPath(from), { replace: true });
	}, [from, navigate]);

	// Passkey login handler (defined early for use in auto-trigger effect)
	const handlePasskeyLogin = useCallback(async () => {
		setError(null);
		setIsPasskeyLoading(true);

		try {
			// Pass email if user has entered one (helps target specific credentials)
			await loginWithPasskey(email || undefined);
			setFinalizing("Signing you in…");
			redirectToFrom();
		} catch (err) {
			// Don't show error for user cancellation
			if (err instanceof Error) {
				if (
					err.message.includes("cancelled") ||
					err.message.includes("not allowed")
				) {
					// User cancelled, just reset loading state
					setIsPasskeyLoading(false);
					return;
				}
				setError(err.message);
			} else {
				setError("Passkey authentication failed");
			}
		} finally {
			setIsPasskeyLoading(false);
		}
	}, [email, loginWithPasskey, redirectToFrom]);

	const handleOAuthLogin = useCallback(
		async (provider: string) => {
			setError(null);
			setIsLoading(true);

			try {
				// Store redirect info for callback
				// Note: PKCE (code_verifier) is now handled server-side
				sessionStorage.setItem(
					"oauth_redirect_from",
					toSameOriginPath(from),
				);

				// Build callback URL
				const callbackUrl = `${window.location.origin}/auth/callback/${provider}`;

				// Get authorization URL (server generates and stores PKCE verifier)
				const { authorization_url, state } = await initOAuth(
					provider,
					callbackUrl,
				);

				// Store only a digest of the state for the callback CSRF check —
				// never the raw token (see hashOAuthState).
				sessionStorage.setItem(
					"oauth_state",
					await hashOAuthState(state),
				);

				// Redirect to OAuth provider
				setFinalizing("Redirecting to sign-in…");
				window.location.assign(authorization_url);
			} catch (err) {
				setError(
					err instanceof Error
						? err.message
						: "OAuth initialization failed",
				);
				setFinalizing(null);
				setIsLoading(false);
			}
		},
		[from],
	);

	// Load the full login policy before presenting or starting an auth method.
	useEffect(() => {
		getAuthStatus()
			.then((status) => {
				setOAuthProviders(status.oauth_providers);
				setAutoRedirectToSso(
					status.auto_redirect_to_sso && !status.needs_setup,
				);
				setPasswordLoginEnabled(status.password_login_enabled);
				setPasskeyLoginEnabled(status.passkey_login_enabled);
				setDefaultSsoProvider(status.default_sso_provider ?? null);
			})
			.catch(() => {
				// If status cannot load, leave all optional methods disabled and
				// present the standard login screen.
			})
			.finally(() => setAuthStatusLoaded(true));
	}, []);

	// The preference is one attempt, not an enforcement loop. Set the guard
	// before leaving so browser Back and provider cancellation show the full
	// login screen on return.
	useEffect(() => {
		if (
			!authStatusLoaded ||
			authLoading ||
			isAuthenticated ||
			step !== "credentials" ||
			!autoRedirectToSso ||
			!defaultSsoProvider ||
			sessionStorage.getItem(PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY)
		) {
			return;
		}

		sessionStorage.setItem(PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY, "true");
		queueMicrotask(() => void handleOAuthLogin(defaultSsoProvider));
	}, [
		authLoading,
		authStatusLoaded,
		autoRedirectToSso,
		defaultSsoProvider,
		handleOAuthLogin,
		isAuthenticated,
		step,
	]);

	// Browsers may restore the pre-redirect page from the back-forward cache.
	// Clear its transient loading state while keeping the attempt guard.
	useEffect(() => {
		const handlePageShow = () => {
			if (sessionStorage.getItem(PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY)) {
				setFinalizing(null);
				setIsLoading(false);
			}
		};
		window.addEventListener("pageshow", handlePageShow);
		return () => window.removeEventListener("pageshow", handlePageShow);
	}, []);

	// Auto-trigger passkey authentication if:
	useEffect(() => {
		const supported = passkeySupported;

		// Conditions:
		// - Browser supports passkeys
		// - Not already authenticated
		// - Haven't already attempted this session
		// - Not in MFA step
		const hasAttempted = sessionStorage.getItem("passkey_auto_attempted");
		if (
			supported &&
			authStatusLoaded &&
			!autoRedirectToSso &&
			passkeyLoginEnabled &&
			!authLoading &&
			!isAuthenticated &&
			!hasAttempted &&
			step === "credentials"
		) {
			sessionStorage.setItem("passkey_auto_attempted", "true");
			// Slight delay to let the page render first
			const timer = setTimeout(() => {
				handlePasskeyLogin();
			}, 300);
			return () => clearTimeout(timer);
		}
		return undefined;
	}, [
		authLoading,
		authStatusLoaded,
		autoRedirectToSso,
		isAuthenticated,
		passkeyLoginEnabled,
		step,
		handlePasskeyLogin,
		passkeySupported,
	]);

	// Clear the auto-attempt flag when user logs out and returns
	useEffect(() => {
		// If user navigates away from login, clear the flag so it triggers next time
		return () => {
			// Don't clear if we're navigating due to successful auth
			if (!isAuthenticated) {
				sessionStorage.removeItem("passkey_auto_attempted");
			}
		};
	}, [isAuthenticated]);

	// Redirect if already authenticated
	useEffect(() => {
		if (!authLoading && isAuthenticated) {
			redirectToFrom();
		}
	}, [authLoading, isAuthenticated, redirectToFrom]);

	const handleCredentialsSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		setIsLoading(true);

		try {
			const result = await login(email, password);

			if (result.success) {
				setFinalizing("Signing you in…");
				redirectToFrom();
				return;
			}

			if (result.mfaRequired) {
				setMfaState({
					mfaToken: result.mfaToken!,
					availableMethods: result.availableMethods!,
					expiresIn: result.expiresIn!,
				});
				setStep("mfa");
				return;
			}

			if (result.mfaSetupRequired) {
				// Redirect to MFA setup with the token
				navigate("/mfa-setup", {
					state: { mfaToken: result.mfaToken, from },
				});
				return;
			}
		} catch (err) {
			setError(err instanceof Error ? err.message : "Login failed");
		} finally {
			setIsLoading(false);
		}
	};

	const handleMfaSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!mfaState) return;

		setError(null);
		setIsLoading(true);

		try {
			await loginWithMfa(mfaState.mfaToken, mfaCode, trustDevice);
			setFinalizing("Signing you in…");
			redirectToFrom();
		} catch (err) {
			setError(
				err instanceof Error ? err.message : "MFA verification failed",
			);
		} finally {
			setIsLoading(false);
		}
	};

	const getProviderIcon = (provider: string) => {
		switch (provider) {
			case "microsoft":
				return <MicrosoftBrandIcon className="h-5 w-5" />;
			case "google":
				return <GoogleBrandIcon className="h-5 w-5" />;
			default:
				return <KeyRound className="w-5 h-5" />;
		}
	};

	if (finalizing) {
		return <AuthTransition message={finalizing} />;
	}

	if (authLoading || !authStatusLoaded) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-background">
				<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
			</div>
		);
	}

	return (
		<div className="min-h-svh flex items-center justify-center bg-background px-4 py-8">
			<motion.div
				initial={reducedMotion ? false : { opacity: 0, y: 4 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{
					duration: reducedMotion ? 0 : 0.36,
					ease: "easeOut",
				}}
				className="w-full max-w-md"
			>
				<Card className="rounded-[var(--bf-radius-feature)] border-border shadow-none">
					<CardHeader className="text-center space-y-4 pb-2">
						<motion.div
							initial={reducedMotion ? false : { opacity: 0 }}
							animate={{ opacity: 1 }}
							transition={{ duration: reducedMotion ? 0 : 0.22 }}
							className="flex justify-center"
						>
							<Logo
								type="square"
								className="h-16 w-16"
								alt={applicationName}
							/>
						</motion.div>
						<div className="space-y-1">
							<h1 className="font-display text-2xl font-semibold tracking-tight">
								{applicationName}
							</h1>
							<CardDescription className="text-base">
								{step === "credentials" &&
									"Sign in to your account"}
								{step === "mfa" && "Two-Factor Authentication"}
								{step === "mfa-setup" &&
									"Set up Two-Factor Authentication"}
							</CardDescription>
						</div>
					</CardHeader>
					<CardContent>
						{accountMessage && step === "credentials" && !error && (
							<Alert role="status" className="mb-4">
								<AlertDescription>
									{accountMessage}
								</AlertDescription>
							</Alert>
						)}
						{error && (
							<Alert variant="destructive" className="mb-4">
								<AlertDescription>{error}</AlertDescription>
							</Alert>
						)}

						{step === "credentials" && (
							<>
								{/* Passkey login (if supported and allowed) */}
								{passkeyAllowed && (
									<>
										<Button
											type="button"
											variant="outline"
											className="min-h-11 w-full mb-4"
											onClick={handlePasskeyLogin}
											disabled={
												isLoading || isPasskeyLoading
											}
										>
											{isPasskeyLoading ? (
												<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none mr-2" />
											) : (
												<Fingerprint className="h-4 w-4 mr-2" />
											)}
											Sign in with passkey
										</Button>

										{!passwordHiddenByPolicy && (
											<div className="relative mb-4">
												<div className="absolute inset-0 flex items-center">
													<span className="w-full border-t" />
												</div>
												<div className="relative flex justify-center text-xs uppercase">
													<span className="bg-card px-2 text-muted-foreground">
														Or use email
													</span>
												</div>
											</div>
										)}
									</>
								)}

								{!passwordHiddenByPolicy && (
									<form
										onSubmit={handleCredentialsSubmit}
										className="space-y-4"
									>
										<div className="space-y-2">
											<Label htmlFor="email">Email</Label>
											<div className="relative">
												<Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
												<Input
													id="email"
													type="email"
													placeholder="you@example.com"
													value={email}
													onChange={(e) =>
														setEmail(e.target.value)
													}
													className="h-11 pl-10"
													required
													autoFocus
												/>
											</div>
										</div>
										<div className="space-y-2">
											<Label htmlFor="password">
												Password
											</Label>
											<div className="relative">
												<Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
												<Input
													id="password"
													type="password"
													placeholder="Enter your password"
													value={password}
													onChange={(e) =>
														setPassword(
															e.target.value,
														)
													}
													className="h-11 pl-10"
													required
												/>
											</div>
										</div>
										<Button
											type="submit"
											className="min-h-11 w-full"
											disabled={
												isLoading || !email || !password
											}
										>
											{isLoading ? (
												<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none mr-2" />
											) : null}
											Sign In
										</Button>
									</form>
								)}

								{showAdminExpander && (
									<details className="mt-6 rounded-md border border-dashed px-4 py-3">
										<summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium">
											Excluded Account Sign-In
										</summary>
										<p className="pt-1 text-sm text-muted-foreground">
											For accounts excluded from the login
											policy. Other accounts continue with
											SSO.
										</p>
										<div className="space-y-4 pt-4">
											{passkeyHiddenByPolicy && (
												<Button
													type="button"
													variant="outline"
													className="min-h-11 w-full"
													onClick={handlePasskeyLogin}
													disabled={
														isLoading ||
														isPasskeyLoading
													}
												>
													{isPasskeyLoading ? (
														<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none mr-2" />
													) : (
														<Fingerprint className="h-4 w-4 mr-2" />
													)}
													Sign in with passkey
												</Button>
											)}
											{passwordHiddenByPolicy && (
												<form
													onSubmit={
														handleCredentialsSubmit
													}
													className="space-y-4"
												>
													<div className="space-y-2">
														<Label htmlFor="email">
															Email
														</Label>
														<div className="relative">
															<Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
															<Input
																id="email"
																type="email"
																placeholder="you@example.com"
																value={email}
																onChange={(e) =>
																	setEmail(
																		e.target
																			.value,
																	)
																}
																className="h-11 pl-10"
																required
															/>
														</div>
													</div>
													<div className="space-y-2">
														<Label htmlFor="password">
															Password
														</Label>
														<div className="relative">
															<Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
															<Input
																id="password"
																type="password"
																placeholder="Enter your password"
																value={password}
																onChange={(e) =>
																	setPassword(
																		e.target
																			.value,
																	)
																}
																className="h-11 pl-10"
																required
															/>
														</div>
													</div>
													<Button
														type="submit"
														className="min-h-11 w-full"
														disabled={
															isLoading ||
															!email ||
															!password
														}
													>
														{isLoading ? (
															<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none mr-2" />
														) : null}
														Sign In
													</Button>
												</form>
											)}
										</div>
									</details>
								)}
								{oauthProviders.length > 0 && (
									<>
										<div className="relative my-6">
											<div className="absolute inset-0 flex items-center">
												<span className="w-full border-t" />
											</div>
											<div className="relative flex justify-center text-xs uppercase">
												<span className="bg-card px-2 text-muted-foreground">
													Or continue with
												</span>
											</div>
										</div>

										<div className="grid gap-2">
											{oauthProviders.map((provider) => (
												<Button
													key={provider.name}
													variant="outline"
													onClick={() =>
														handleOAuthLogin(
															provider.name,
														)
													}
													disabled={isLoading}
													className="min-h-11 w-full"
												>
													{getProviderIcon(
														provider.name,
													)}
													<span className="ml-2">
														{provider.display_name}
													</span>
													<ExternalLink className="ml-auto h-4 w-4 text-muted-foreground" />
												</Button>
											))}
										</div>
									</>
								)}
							</>
						)}

						{step === "mfa" && (
							<form
								onSubmit={handleMfaSubmit}
								className="space-y-4"
							>
								<div className="space-y-2">
									<Label htmlFor="mfaCode">
										Authentication Code
									</Label>
									<div className="relative">
										<KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
										<Input
											id="mfaCode"
											type="text"
											placeholder="Enter 6-digit code or recovery code"
											value={mfaCode}
											onChange={(e) =>
												setMfaCode(e.target.value)
											}
											className="h-11 pl-10 font-mono text-center text-lg tracking-widest"
											maxLength={9}
											autoFocus
										/>
									</div>
									<p className="text-xs text-muted-foreground">
										Enter the code from your authenticator
										app, or use a recovery code.
									</p>
								</div>

								<div className="flex items-center space-x-2">
									<Checkbox
										id="trustDevice"
										checked={trustDevice}
										onCheckedChange={(checked) =>
											setTrustDevice(checked === true)
										}
									/>
									<Label
										htmlFor="trustDevice"
										className="text-sm font-normal"
									>
										Trust this device for 30 days
									</Label>
								</div>

								<Button
									type="submit"
									className="min-h-11 w-full"
									disabled={isLoading || mfaCode.length < 6}
								>
									{isLoading ? (
										<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none mr-2" />
									) : null}
									Verify
								</Button>

								<Button
									type="button"
									variant="ghost"
									className="min-h-11 w-full"
									onClick={() => {
										setStep("credentials");
										setMfaCode("");
										setMfaState(null);
									}}
								>
									Back to login
								</Button>
							</form>
						)}
					</CardContent>
				</Card>
			</motion.div>
		</div>
	);
}

export default Login;
