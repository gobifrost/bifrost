/**
 * Login Page
 *
 * Handles email/password login with MFA flow, OAuth, and passkey options.
 */

import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { getOAuthProviders, hashOAuthState, initOAuth } from "@/services/auth";
import { supportsPasskeys } from "@/services/passkeys";
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
import { motion } from "framer-motion";
import type { OAuthProvider } from "@/services/auth";
import { Logo } from "@/components/branding/Logo";
import { AuthTransition } from "@/components/auth/AuthTransition";

type LoginStep = "credentials" | "mfa" | "mfa-setup";

interface MFAState {
	mfaToken: string;
	availableMethods: string[];
	expiresIn: number;
}

export function Login() {
	const navigate = useNavigate();
	const location = useLocation();
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
	// Derived synchronously — `supportsPasskeys()` is a pure feature check on
	// `window` that returns the same value across renders for the lifetime of
	// the page, so it does not need to live in state.
	const passkeySupported = useMemo(() => supportsPasskeys(), []);

	// Redirect path from URL query params (for MCP OAuth) or location state
	const searchParams = new URLSearchParams(location.search);
	const returnTo =
		searchParams.get("returnTo") || searchParams.get("return_to");
	const from = returnTo || (location.state as { from?: string })?.from || "/";

	// MCP OAuth returns an absolute URL in return_to; react-router's navigate()
	// treats those as relative paths and produces nonsense like
	// /login/https:/example.com/mcp/callback. For same-origin absolute URLs
	// we route via SPA navigation so components like MCPCallback actually
	// mount (and can issue the Accept: application/json XHR that keeps us on
	// the success screen instead of following the server's 302).
	//
	// Cross-origin URLs are rejected and we fall back to "/" — never honor a
	// foreign origin in `from`, since that's an open-redirect vector.
	const redirectToFrom = useCallback(() => {
		if (from.startsWith("http://") || from.startsWith("https://")) {
			try {
				const url = new URL(from);
				if (url.origin === window.location.origin) {
					navigate(url.pathname + url.search + url.hash, {
						replace: true,
					});
					return;
				}
			} catch {
				// Malformed URL — fall through to safe default.
			}
			// Cross-origin or unparseable absolute URL — refuse to redirect there.
			navigate("/", { replace: true });
		} else {
			navigate(from, { replace: true });
		}
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

	// Load OAuth providers
	useEffect(() => {
		getOAuthProviders()
			.then(setOAuthProviders)
			.catch(() => {
				// OAuth not configured, that's fine
			});
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
	}, [authLoading, isAuthenticated, step, handlePasskeyLogin, passkeySupported]);

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

	const handleOAuthLogin = async (provider: string) => {
		setError(null);
		setIsLoading(true);

		try {
			// Store redirect info for callback
			// Note: PKCE (code_verifier) is now handled server-side
			sessionStorage.setItem("oauth_redirect_from", from);

			// Build callback URL
			const callbackUrl = `${window.location.origin}/auth/callback/${provider}`;

			// Get authorization URL (server generates and stores PKCE verifier)
			const { authorization_url, state } = await initOAuth(
				provider,
				callbackUrl,
			);

			// Store only a digest of the state for the callback CSRF check —
			// never the raw token (see hashOAuthState).
			sessionStorage.setItem("oauth_state", await hashOAuthState(state));

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
	};

	const getProviderIcon = (provider: string) => {
		switch (provider) {
			case "microsoft":
				return (
					<svg className="w-5 h-5" viewBox="0 0 21 21" fill="none">
						<rect x="1" y="1" width="9" height="9" fill="#F25022" />
						<rect
							x="11"
							y="1"
							width="9"
							height="9"
							fill="#7FBA00"
						/>
						<rect
							x="1"
							y="11"
							width="9"
							height="9"
							fill="#00A4EF"
						/>
						<rect
							x="11"
							y="11"
							width="9"
							height="9"
							fill="#FFB900"
						/>
					</svg>
				);
			case "google":
				return (
					<svg className="w-5 h-5" viewBox="0 0 24 24">
						<path
							fill="#4285F4"
							d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
						/>
						<path
							fill="#34A853"
							d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
						/>
						<path
							fill="#FBBC05"
							d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
						/>
						<path
							fill="#EA4335"
							d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
						/>
					</svg>
				);
			default:
				return <KeyRound className="w-5 h-5" />;
		}
	};

	if (finalizing) {
		return <AuthTransition message={finalizing} />;
	}

	if (authLoading) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-background">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	return (
		<div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-primary/5 p-4">
			<motion.div
				initial={{ opacity: 0, y: 20 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.4, ease: "easeOut" }}
				className="w-full max-w-md"
			>
				<Card className="border-primary/10 shadow-xl shadow-primary/5">
					<CardHeader className="text-center space-y-4 pb-2">
						<motion.div
							initial={{ scale: 0.8, opacity: 0 }}
							animate={{ scale: 1, opacity: 1 }}
							transition={{ delay: 0.1, duration: 0.3 }}
							className="flex justify-center"
						>
							<Logo
								type="square"
								className="h-16 w-16"
								alt="Bifrost"
							/>
						</motion.div>
						<div className="space-y-1">
							<h1 className="text-2xl font-bold tracking-tight">
								Bifrost
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
						{error && (
							<Alert variant="destructive" className="mb-4">
								<AlertDescription>{error}</AlertDescription>
							</Alert>
						)}

						{step === "credentials" && (
							<>
								{/* Passkey login (if supported) */}
								{passkeySupported && (
									<>
										<Button
											type="button"
											variant="outline"
											className="w-full mb-4"
											onClick={handlePasskeyLogin}
											disabled={
												isLoading || isPasskeyLoading
											}
										>
											{isPasskeyLoading ? (
												<Loader2 className="h-4 w-4 animate-spin mr-2" />
											) : (
												<Fingerprint className="h-4 w-4 mr-2" />
											)}
											Sign in with passkey
										</Button>

										<div className="relative mb-4">
											<div className="absolute inset-0 flex items-center">
												<span className="w-full border-t" />
											</div>
											<div className="relative flex justify-center text-xs uppercase">
												<span className="bg-background px-2 text-muted-foreground">
													Or use email
												</span>
											</div>
										</div>
									</>
								)}

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
												className="pl-10"
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
													setPassword(e.target.value)
												}
												className="pl-10"
												required
											/>
										</div>
									</div>
									<Button
										type="submit"
										className="w-full"
										disabled={
											isLoading || !email || !password
										}
									>
										{isLoading ? (
											<Loader2 className="h-4 w-4 animate-spin mr-2" />
										) : null}
										Sign In
									</Button>
								</form>

								{oauthProviders.length > 0 && (
									<>
										<div className="relative my-6">
											<div className="absolute inset-0 flex items-center">
												<span className="w-full border-t" />
											</div>
											<div className="relative flex justify-center text-xs uppercase">
												<span className="bg-background px-2 text-muted-foreground">
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
													className="w-full"
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
											placeholder="Enter 6-digit code"
											value={mfaCode}
											onChange={(e) =>
												setMfaCode(e.target.value)
											}
											className="pl-10 text-center text-lg tracking-widest"
											maxLength={8}
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
									className="w-full"
									disabled={isLoading || mfaCode.length < 6}
								>
									{isLoading ? (
										<Loader2 className="h-4 w-4 animate-spin mr-2" />
									) : null}
									Verify
								</Button>

								<Button
									type="button"
									variant="ghost"
									className="w-full"
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
