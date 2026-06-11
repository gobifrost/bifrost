/**
 * MFA Setup Page
 *
 * For users who need to set up MFA after password login.
 * Accessed when login returns mfa_setup_required.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
// Note: This component uses direct fetch calls rather than the auth context
// because it handles the MFA setup flow before full authentication is complete
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
	Loader2,
	Shield,
	Copy,
	Download,
	CheckCircle,
	RefreshCw,
	AlertCircle,
} from "lucide-react";
import { QRCode } from "@/components/ui/QRCode";
import { toast } from "sonner";

type SetupStep = "setup" | "verify" | "recovery-codes";

interface TOTPSetup {
	secret: string;
	qrCodeUri: string;
	isExisting?: boolean;
}

export function MFASetup() {
	const navigate = useNavigate();
	const location = useLocation();

	const [step, setStep] = useState<SetupStep>("setup");
	const [isLoading, setIsLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// Track if we've already initialized to prevent double-fetch
	const hasInitialized = useRef(false);

	// Get MFA token from location state or sessionStorage (backup for page refreshes)
	const locationMfaToken = (location.state as { mfaToken?: string })
		?.mfaToken;
	const from = (location.state as { from?: string })?.from || "/";

	// Use location state token if available, otherwise fall back to sessionStorage
	const [mfaToken] = useState<string | undefined>(() => {
		if (locationMfaToken) {
			// Store in sessionStorage as backup
			sessionStorage.setItem("mfa_setup_token", locationMfaToken);
			return locationMfaToken;
		}
		// Try to recover from sessionStorage
		return sessionStorage.getItem("mfa_setup_token") || undefined;
	});

	// MFA state
	const [totpSetup, setTotpSetup] = useState<TOTPSetup | null>(null);
	const [mfaCode, setMfaCode] = useState("");
	const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
	const [recoveryCodesSaved, setRecoveryCodesSaved] = useState(false);
	const [secretCopied, setSecretCopied] = useState(false);

	const initMfaSetup = useCallback(
		async (forceNew: boolean = false) => {
			setIsLoading(true);
			setError(null);
			try {
				// Call setup endpoint with the MFA setup token
				const res = await fetch("/auth/mfa/setup", {
					method: "POST",
					headers: {
						"Content-Type": "application/json",
						Authorization: `Bearer ${mfaToken}`,
					},
					body: forceNew
						? JSON.stringify({ force_new: true })
						: undefined,
				});

				// 401 means the MFA token is expired or invalid - redirect to login
				if (res.status === 401) {
					sessionStorage.removeItem("mfa_setup_token");
					toast.error("Session expired. Please log in again.");
					navigate("/login", { state: { from } });
					return;
				}

				if (!res.ok) {
					const errorData = await res.json().catch(() => ({}));
					throw new Error(
						errorData.detail || "Failed to initialize MFA setup",
					);
				}

				const data = await res.json();
				setTotpSetup({
					secret: data.secret,
					qrCodeUri: data.qr_code_uri,
					isExisting: data.is_existing,
				});

				// If this is an existing setup, inform the user
				if (data.is_existing && !forceNew) {
					toast.info(
						"Resuming previous setup. If you need a new QR code, click 'Generate New Code'.",
					);
				}
			} catch (err) {
				const message =
					err instanceof Error
						? err.message
						: "Failed to initialize MFA";
				setError(message);

				// Provide specific guidance based on error
				if (
					message.includes("expired") ||
					message.includes("invalid")
				) {
					setError(
						"Your session has expired. Please log in again to continue MFA setup.",
					);
				}
			} finally {
				setIsLoading(false);
			}
		},
		[mfaToken, navigate, from],
	);

	// Redirect if no MFA token
	useEffect(() => {
		if (!mfaToken) {
			navigate("/login");
			return;
		}

		// Only initialize once to prevent secret regeneration on component remount
		if (!hasInitialized.current) {
			hasInitialized.current = true;
			initMfaSetup();
		}
	}, [mfaToken, navigate, initMfaSetup]);

	const handleGenerateNew = () => {
		// Explicitly request a new secret
		initMfaSetup(true);
	};

	const handleMfaVerify = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		setIsLoading(true);

		try {
			const res = await fetch("/auth/mfa/verify", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
					Authorization: `Bearer ${mfaToken}`,
				},
				body: JSON.stringify({ code: mfaCode }),
			});

			// 401 means the MFA token is expired or invalid - redirect to login
			if (res.status === 401) {
				sessionStorage.removeItem("mfa_setup_token");
				toast.error("Session expired. Please log in again.");
				navigate("/login", { state: { from } });
				return;
			}

			if (!res.ok) {
				const errorData = await res.json().catch(() => ({}));
				const detail = errorData.detail || "Invalid code";

				// Provide more helpful error messages
				if (detail.toLowerCase().includes("invalid")) {
					throw new Error(
						"Invalid code. Please check that you entered the correct 6-digit code from your authenticator app.",
					);
				}
				throw new Error(detail);
			}

			const data = await res.json();
			if (data.success) {
				// Clear the MFA setup token from sessionStorage
				sessionStorage.removeItem("mfa_setup_token");
				setRecoveryCodes(data.recovery_codes);
				setStep("recovery-codes");
				toast.success("MFA setup complete!");
			}
		} catch (err) {
			setError(
				err instanceof Error ? err.message : "Verification failed",
			);
		} finally {
			setIsLoading(false);
		}
	};

	const copySecret = async () => {
		if (totpSetup?.secret) {
			await navigator.clipboard.writeText(totpSetup.secret);
			setSecretCopied(true);
			toast.success("Secret copied to clipboard");
			setTimeout(() => setSecretCopied(false), 2000);
		}
	};

	const copyRecoveryCodes = async () => {
		const text = recoveryCodes.join("\n");
		await navigator.clipboard.writeText(text);
		toast.success("Recovery codes copied to clipboard");
	};

	const downloadRecoveryCodes = () => {
		const text = `Bifrost Recovery Codes
Generated: ${new Date().toISOString()}

These codes can be used to access your account if you lose your authenticator device.
Each code can only be used once.

${recoveryCodes.join("\n")}

Keep these codes in a secure location.
`;
		const blob = new Blob([text], { type: "text/plain" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = "bifrost-recovery-codes.txt";
		a.click();
		URL.revokeObjectURL(url);
		toast.success("Recovery codes downloaded");
	};

	const handleComplete = () => {
		// Redirect back to login to complete with MFA
		navigate("/login", { state: { from } });
	};

	if (isLoading && !totpSetup) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-background">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	return (
		<div className="min-h-screen flex items-center justify-center bg-background p-4">
			<Card className="w-full max-w-md">
				<CardHeader className="text-center">
					<CardTitle className="text-2xl font-bold">
						<Shield className="h-8 w-8 mx-auto mb-2" />
						Two-Factor Authentication
					</CardTitle>
					<CardDescription>
						{step === "setup" &&
							"Scan the QR code with your authenticator app"}
						{step === "verify" && "Enter the verification code"}
						{step === "recovery-codes" &&
							"Save your recovery codes"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					{error && (
						<Alert variant="destructive" className="mb-4">
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}

					<Alert className="mb-4">
						<AlertDescription>
							Two-factor authentication is required for
							password-based login. This helps protect your
							account from unauthorized access.
						</AlertDescription>
					</Alert>

					{step === "setup" && totpSetup && (
						<div className="space-y-4">
							<div className="flex items-center justify-center p-4 bg-white rounded-lg">
								<QRCode
									data={totpSetup.qrCodeUri}
									size={200}
									alt="TOTP QR Code"
								/>
							</div>

							<div className="text-center space-y-2">
								<p className="text-sm text-muted-foreground">
									Scan this QR code with your authenticator
									app
								</p>
								<p className="text-xs text-muted-foreground">
									Or enter this code manually:
								</p>
								<div className="flex items-center justify-center gap-2">
									<code className="text-sm bg-muted px-2 py-1 rounded font-mono">
										{totpSetup.secret}
									</code>
									<Button
										variant="ghost"
										size="sm"
										onClick={copySecret}
										className="h-7 px-2"
									>
										{secretCopied ? (
											<CheckCircle className="h-3 w-3 text-green-500" />
										) : (
											<Copy className="h-3 w-3" />
										)}
									</Button>
								</div>
							</div>

							<Button
								onClick={() => setStep("verify")}
								className="w-full"
							>
								<Shield className="h-4 w-4 mr-2" />
								I've added the code
							</Button>

							<Button
								variant="ghost"
								size="sm"
								onClick={handleGenerateNew}
								disabled={isLoading}
								className="w-full text-muted-foreground"
							>
								{isLoading ? (
									<Loader2 className="h-4 w-4 animate-spin mr-2" />
								) : (
									<RefreshCw className="h-4 w-4 mr-2" />
								)}
								Generate new code
							</Button>
						</div>
					)}

					{step === "verify" && (
						<form onSubmit={handleMfaVerify} className="space-y-4">
							<div className="space-y-2">
								<Label htmlFor="verifyCode">
									Verification Code
								</Label>
								<Input
									id="verifyCode"
									type="text"
									inputMode="numeric"
									pattern="[0-9]*"
									placeholder="Enter 6-digit code"
									value={mfaCode}
									onChange={(e) =>
										setMfaCode(
											e.target.value.replace(/\D/g, ""),
										)
									}
									className="text-center text-lg tracking-widest"
									maxLength={6}
									autoFocus
								/>
								<p className="text-xs text-muted-foreground text-center">
									Enter the code from your authenticator app
								</p>
							</div>

							<Button
								type="submit"
								className="w-full"
								disabled={isLoading || mfaCode.length !== 6}
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
									setStep("setup");
									setMfaCode("");
									setError(null);
								}}
							>
								Back to QR code
							</Button>
						</form>
					)}

					{step === "recovery-codes" && (
						<div className="space-y-4">
							<Alert>
								<AlertDescription>
									Save these recovery codes in a secure
									location. Each code can only be used once to
									access your account if you lose your
									authenticator.
								</AlertDescription>
							</Alert>

							<div className="grid grid-cols-2 gap-2 p-4 bg-muted rounded-lg font-mono text-sm ring-1 ring-foreground/5">
								{recoveryCodes.map((code, i) => (
									<div key={i} className="text-center py-1">
										{code}
									</div>
								))}
							</div>

							<div className="flex gap-2">
								<Button
									variant="outline"
									className="flex-1"
									onClick={copyRecoveryCodes}
								>
									<Copy className="h-4 w-4 mr-2" />
									Copy
								</Button>
								<Button
									variant="outline"
									className="flex-1"
									onClick={downloadRecoveryCodes}
								>
									<Download className="h-4 w-4 mr-2" />
									Download
								</Button>
							</div>

							<div className="flex items-center space-x-2">
								<Checkbox
									id="savedCodes"
									checked={recoveryCodesSaved}
									onCheckedChange={(checked) =>
										setRecoveryCodesSaved(checked === true)
									}
								/>
								<Label
									htmlFor="savedCodes"
									className="text-sm font-normal"
								>
									I have saved my recovery codes
								</Label>
							</div>

							<Button
								onClick={handleComplete}
								className="w-full"
								disabled={!recoveryCodesSaved}
							>
								<CheckCircle className="h-4 w-4 mr-2" />
								Continue to Login
							</Button>
						</div>
					)}
				</CardContent>
			</Card>
		</div>
	);
}

export default MFASetup;
