import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { ExternalLink, KeyRound, Loader2 } from "lucide-react";
import { motion } from "framer-motion";
import { Logo } from "@/components/branding/Logo";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AuthSetupSteps } from "@/components/auth/AuthSetupSteps";
import { AuthTransition } from "@/components/auth/AuthTransition";
import { useAuth } from "@/contexts/AuthContext";
import {
	getOAuthProviders,
	hashOAuthState,
	initOAuth,
	registerFromInvite,
	type OAuthProvider,
} from "@/services/auth";
import { registerInviteWithPasskey } from "@/services/passkeys";

export function Register() {
	const [params] = useSearchParams();
	const token = params.get("token") ?? "";
	const [error, setError] = useState<string | null>(null);
	const [pending, setPending] = useState(false);
	const [finalizing, setFinalizing] = useState<string | null>(null);
	const [oauthProviders, setOAuthProviders] = useState<OAuthProvider[]>([]);
	const nav = useNavigate();
	const { completeLoginWithToken, checkAuthStatus } = useAuth();

	useEffect(() => {
		getOAuthProviders()
			.then(setOAuthProviders)
			.catch(() => setOAuthProviders([]));
	}, []);

	if (!token) {
		return (
			<div className="min-h-screen flex items-center justify-center p-8">
				<p className="text-muted-foreground">Missing invite token.</p>
			</div>
		);
	}

	const handlePasskeyRegister = async () => {
		setPending(true);
		setError(null);
		try {
			const result = await registerInviteWithPasskey(token);
			setFinalizing("Signing you in…");
			completeLoginWithToken(result.access_token);
			await checkAuthStatus();
			nav("/", { replace: true });
		} catch (e: unknown) {
			const msg =
				e instanceof Error
					? e.message
					: ((e as { detail?: string })?.detail ??
						"Registration failed");
			setError(msg);
			setFinalizing(null);
		} finally {
			setPending(false);
		}
	};

	const handlePasswordRegister = async (password: string) => {
		setPending(true);
		setError(null);
		try {
			await registerFromInvite(token, password);
			setFinalizing("Creating your account…");
			nav("/login", {
				state: { message: "Registration complete! Please sign in." },
			});
		} catch (e: unknown) {
			const msg =
				e instanceof Error
					? e.message
					: ((e as { detail?: string })?.detail ??
						"Registration failed");
			setError(msg);
			setFinalizing(null);
		} finally {
			setPending(false);
		}
	};

	const handleOAuthLogin = async (provider: string) => {
		setPending(true);
		setError(null);
		try {
			sessionStorage.setItem("oauth_redirect_from", "/");
			const callbackUrl = `${window.location.origin}/auth/callback/${provider}`;
			const { authorization_url, state } = await initOAuth(
				provider,
				callbackUrl,
			);
			sessionStorage.setItem("oauth_state", await hashOAuthState(state));
			setFinalizing("Redirecting to sign-in…");
			window.location.assign(authorization_url);
		} catch (e: unknown) {
			const msg =
				e instanceof Error ? e.message : "SSO initialization failed";
			setError(msg);
			setFinalizing(null);
			setPending(false);
		}
	};

	if (finalizing) {
		return <AuthTransition message={finalizing} />;
	}

	return (
		<div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-primary/5 p-4">
			<Card className="w-full max-w-md border-primary/10 shadow-xl shadow-primary/5">
				<CardHeader className="text-center space-y-4 pb-2">
					<motion.div
						initial={{ scale: 0.8, opacity: 0 }}
						animate={{ scale: 1, opacity: 1 }}
						transition={{ delay: 0.1, duration: 0.3 }}
						className="flex justify-center"
					>
						<Logo type="square" className="h-16 w-16" alt="Bifrost" />
					</motion.div>
					<div className="space-y-1">
						<h1 className="text-2xl font-bold tracking-tight">
							Complete your registration
						</h1>
						<CardDescription>
							Secure your account to accept the invite.
						</CardDescription>
					</div>
				</CardHeader>
				<CardContent>
					{error && (
						<Alert variant="destructive" className="mb-4">
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					<div className="space-y-4">
						{oauthProviders.length > 0 && (
							<>
								<div className="grid gap-2">
									{oauthProviders.map((provider) => (
										<Button
											key={provider.name}
											type="button"
											variant="outline"
											onClick={() =>
												handleOAuthLogin(provider.name)
											}
											disabled={pending}
											className="w-full"
										>
											{pending ? (
												<Loader2 className="h-4 w-4 animate-spin mr-2" />
											) : (
												<KeyRound className="h-4 w-4 mr-2" />
											)}
											Continue with{" "}
											{provider.display_name}
											<ExternalLink className="ml-auto h-4 w-4 text-muted-foreground" />
										</Button>
									))}
								</div>
								<div className="relative">
									<div className="absolute inset-0 flex items-center">
										<span className="w-full border-t" />
									</div>
									<div className="relative flex justify-center text-xs uppercase">
										<span className="bg-background px-2 text-muted-foreground">
											Or
										</span>
									</div>
								</div>
							</>
						)}
						<AuthSetupSteps
							email=""
							onPasskeyRegister={handlePasskeyRegister}
							onPasswordRegister={handlePasswordRegister}
							isPending={pending}
							error={null}
						/>
					</div>
				</CardContent>
			</Card>
		</div>
	);
}

export default Register;
