/**
 * Auth Callback Page
 *
 * Handles OAuth callback from identity providers.
 * Exchanges authorization code for tokens and redirects to the app.
 */

import { useEffect, useState, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
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

			try {
				// Exchange code for tokens (server handles PKCE verification)
				await loginWithOAuth(provider, code, state);

				navigate("/", { replace: true });
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
