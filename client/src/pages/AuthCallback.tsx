/**
 * Auth Callback Page
 *
 * Handles OAuth callback from identity providers.
 * Exchanges authorization code for tokens and redirects to the app.
 */

import { useEffect, useState, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { hashOAuthState } from "@/services/auth";
import { Loader2, AlertCircle } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export function AuthCallback() {
	const navigate = useNavigate();
	const { provider } = useParams<{ provider: string }>();
	const [searchParams] = useSearchParams();
	const { loginWithOAuth } = useAuth();

	const [error, setError] = useState<string | null>(null);
	const hasHandledRef = useRef(false);

	useEffect(() => {
		// Prevent double execution in strict mode
		if (hasHandledRef.current) return;
		hasHandledRef.current = true;

		async function handleCallback() {
			// Get OAuth response parameters
			const code = searchParams.get("code");
			const state = searchParams.get("state");
			const errorParam = searchParams.get("error");
			const errorDescription = searchParams.get("error_description");

			// Check for OAuth error
			if (errorParam) {
				setError(errorDescription || errorParam);
				return;
			}

			// Verify required parameters
			if (!code || !state || !provider) {
				setError("Missing required OAuth parameters");
				return;
			}

			// Get stored state digest
			// Note: code_verifier is now handled server-side (stored in Redis when init is called)
			const storedState = sessionStorage.getItem("oauth_state");
			const redirectFrom =
				sessionStorage.getItem("oauth_redirect_from") || "/";

			// Clear stored OAuth data
			sessionStorage.removeItem("oauth_state");
			sessionStorage.removeItem("oauth_redirect_from");

			// Verify state matches. We stored only a digest of the state on init,
			// so compare digests (browser-binding CSRF check; the server also
			// validates the raw state against Redis).
			if (!storedState || (await hashOAuthState(state)) !== storedState) {
				setError("Invalid OAuth state - possible CSRF attack");
				return;
			}

			try {
				// Exchange code for tokens (server handles PKCE verification)
				await loginWithOAuth(provider, code, state);

				// Redirect to original destination
				// Use full page navigation for API routes (bypasses React Router),
				// React Router for normal app routes
				if (redirectFrom.startsWith("/api/")) {
					// Small delay to ensure cookies are fully set before redirect
					await new Promise((resolve) => setTimeout(resolve, 100));
					window.location.href = redirectFrom;
				} else {
					navigate(redirectFrom, { replace: true });
				}
			} catch (err) {
				setError(
					err instanceof Error ? err.message : "OAuth login failed",
				);
			}
		}

		handleCallback();
	}, [searchParams, provider, loginWithOAuth, navigate]);

	if (error) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-background p-4">
				<div className="w-full max-w-md space-y-4">
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{error}</AlertDescription>
					</Alert>
					<Button
						className="w-full"
						onClick={() => navigate("/login")}
					>
						Return to Login
					</Button>
				</div>
			</div>
		);
	}

	return (
		<div className="min-h-screen flex items-center justify-center bg-background">
			<div className="text-center">
				<Loader2 className="h-8 w-8 animate-spin mx-auto mb-4 text-primary" />
				<p className="text-muted-foreground">Completing sign in...</p>
			</div>
		</div>
	);
}

export default AuthCallback;
