/**
 * Security Settings - Passkeys & TOTP Management
 *
 * Allows users to:
 * - Register new passkeys (Face ID, Touch ID, etc.)
 * - View and manage existing passkeys
 * - Set up and manage TOTP (authenticator app)
 * - View and regenerate recovery codes
 */

import { useCallback, useState, useEffect } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Fingerprint,
	Plus,
	Trash2,
	Loader2,
	Info,
	Smartphone,
	Cloud,
	Clock,
	ShieldCheck,
	AlertTriangle,
	Shield,
	Copy,
	Download,
	CheckCircle,
	Key,
	RefreshCw,
} from "lucide-react";
import { usePasskeys, usePasskeySupport } from "@/hooks/usePasskeys";
import type { PasskeyPublic } from "@/services/passkeys";
import {
	mfaService,
	type MFAStatus,
	type MFASetupResponse,
} from "@/services/mfa";
import { QRCode } from "@/components/ui/QRCode";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";

type TOTPSetupStep = "idle" | "setup" | "verify" | "recovery-codes";

export function Security() {
	const support = usePasskeySupport();
	const {
		passkeys,
		passkeyCount,
		isLoading,
		isError,
		register,
		isRegistering,
		delete: deletePasskey,
		isDeleting,
	} = usePasskeys();

	// Passkey dialog state
	const [showAddDialog, setShowAddDialog] = useState(false);
	const [deviceName, setDeviceName] = useState("");
	const [passkeyToDelete, setPasskeyToDelete] =
		useState<PasskeyPublic | null>(null);

	// MFA state
	const [mfaStatus, setMfaStatus] = useState<MFAStatus | null>(null);
	const [mfaLoading, setMfaLoading] = useState(true);
	const [mfaError, setMfaError] = useState<string | null>(null);

	// TOTP setup state
	const [totpStep, setTotpStep] = useState<TOTPSetupStep>("idle");
	const [totpSetup, setTotpSetup] = useState<MFASetupResponse | null>(null);
	const [totpCode, setTotpCode] = useState("");
	const [totpLoading, setTotpLoading] = useState(false);
	const [totpError, setTotpError] = useState<string | null>(null);
	const [secretCopied, setSecretCopied] = useState(false);

	// Recovery codes state
	const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
	const [recoveryCodesSaved, setRecoveryCodesSaved] = useState(false);
	const [showRegenerateDialog, setShowRegenerateDialog] = useState(false);
	const [regenerateCode, setRegenerateCode] = useState("");
	const [regenerateLoading, setRegenerateLoading] = useState(false);

	// Remove MFA state
	const [showRemoveDialog, setShowRemoveDialog] = useState(false);
	const [removeCode, setRemoveCode] = useState("");
	const [removeLoading, setRemoveLoading] = useState(false);

	const loadMFAStatus = useCallback(async () => {
		setMfaLoading(true);
		setMfaError(null);
		try {
			const status = await mfaService.getMFAStatus();
			setMfaStatus(status);
		} catch (err) {
			setMfaError(
				err instanceof Error ? err.message : "Failed to load MFA status",
			);
		} finally {
			setMfaLoading(false);
		}
	}, []);

	// Load MFA status. setState only fires after the awaited fetch resolves
	// — wrapping in a void IIFE keeps the synchronous body free of setState.
	useEffect(() => {
		void (async () => {
			await loadMFAStatus();
		})();
	}, [loadMFAStatus]);

	// Passkey handlers
	const handleRegister = () => {
		register(deviceName || undefined, {
			onSuccess: () => {
				setShowAddDialog(false);
				setDeviceName("");
			},
		});
	};

	const handleDelete = () => {
		if (passkeyToDelete) {
			deletePasskey(passkeyToDelete.id, {
				onSuccess: () => {
					setPasskeyToDelete(null);
				},
			});
		}
	};

	const getDeviceIcon = (deviceType: string) => {
		if (deviceType === "multiDevice") {
			return <Cloud className="h-5 w-5 text-blue-500" />;
		}
		return <Smartphone className="h-5 w-5 text-gray-500" />;
	};

	// TOTP handlers
	const handleStartTOTPSetup = async () => {
		setTotpLoading(true);
		setTotpError(null);
		try {
			const setup = await mfaService.setupTOTP();
			setTotpSetup(setup);
			setTotpStep("setup");
		} catch (err) {
			setTotpError(
				err instanceof Error ? err.message : "Failed to start TOTP setup",
			);
			toast.error("Failed to start TOTP setup");
		} finally {
			setTotpLoading(false);
		}
	};

	const handleVerifyTOTP = async (e: React.FormEvent) => {
		e.preventDefault();
		setTotpLoading(true);
		setTotpError(null);
		try {
			const result = await mfaService.verifyTOTPSetup(totpCode);
			if (result.success && result.recovery_codes) {
				setRecoveryCodes(result.recovery_codes);
				setTotpStep("recovery-codes");
				toast.success("TOTP setup complete!");
			}
		} catch (err) {
			setTotpError(
				err instanceof Error ? err.message : "Invalid verification code",
			);
		} finally {
			setTotpLoading(false);
		}
	};

	const handleCompleteTOTPSetup = () => {
		setTotpStep("idle");
		setTotpSetup(null);
		setTotpCode("");
		setRecoveryCodes([]);
		setRecoveryCodesSaved(false);
		loadMFAStatus();
	};

	const handleRemoveMFA = async () => {
		setRemoveLoading(true);
		try {
			await mfaService.removeMFA({ mfa_code: removeCode });
			toast.success("Two-factor authentication removed");
			setShowRemoveDialog(false);
			setRemoveCode("");
			loadMFAStatus();
		} catch (err) {
			toast.error(
				err instanceof Error ? err.message : "Failed to remove MFA",
			);
		} finally {
			setRemoveLoading(false);
		}
	};

	const handleRegenerateRecoveryCodes = async () => {
		setRegenerateLoading(true);
		try {
			const result = await mfaService.regenerateRecoveryCodes(regenerateCode);
			setRecoveryCodes(result.recovery_codes);
			setShowRegenerateDialog(false);
			setRegenerateCode("");
			setTotpStep("recovery-codes");
			toast.success("Recovery codes regenerated");
		} catch (err) {
			toast.error(
				err instanceof Error ? err.message : "Failed to regenerate codes",
			);
		} finally {
			setRegenerateLoading(false);
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

	// Show loading state for passkeys
	if (support.isLoading) {
		return (
			<Card>
				<CardHeader>
					<Skeleton className="h-6 w-32" />
					<Skeleton className="h-4 w-64 mt-2" />
				</CardHeader>
				<CardContent>
					<Skeleton className="h-20 w-full" />
				</CardContent>
			</Card>
		);
	}

	return (
		<div className="space-y-6">
			{/* Two-Factor Authentication Section */}
			<Card>
				<CardHeader>
					<div className="flex items-center justify-between">
						<div>
							<CardTitle className="flex items-center gap-2">
								<Shield className="h-5 w-5" />
								Two-Factor Authentication
							</CardTitle>
							<CardDescription className="mt-1">
								Add an extra layer of security with an
								authenticator app
							</CardDescription>
						</div>
						{!mfaLoading && mfaStatus && !mfaStatus.mfa_enabled && (
							<Button
								onClick={handleStartTOTPSetup}
								disabled={totpLoading}
							>
								{totpLoading ? (
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
								) : (
									<Plus className="h-4 w-4 mr-2" />
								)}
								Set Up
							</Button>
						)}
					</div>
				</CardHeader>
				<CardContent>
					{/* Loading state */}
					{mfaLoading && (
						<div className="space-y-3">
							<Skeleton className="h-16 w-full" />
						</div>
					)}

					{/* Error state */}
					{mfaError && (
						<Alert variant="destructive">
							<AlertTriangle className="h-4 w-4" />
							<AlertDescription>{mfaError}</AlertDescription>
						</Alert>
					)}

					{/* MFA not enabled */}
					{!mfaLoading && mfaStatus && !mfaStatus.mfa_enabled && totpStep === "idle" && (
						<div className="text-center py-8 text-muted-foreground">
							<Shield className="h-12 w-12 mx-auto mb-4 opacity-50" />
							<p className="font-medium">
								Two-factor authentication not enabled
							</p>
							<p className="text-sm mt-1">
								Protect your account with an authenticator app
							</p>
						</div>
					)}

					{/* MFA enabled */}
					{!mfaLoading && mfaStatus && mfaStatus.mfa_enabled && totpStep === "idle" && (
						<div className="space-y-4">
							<div className="flex items-center justify-between p-4 rounded-lg ring-1 ring-foreground/5">
								<div className="flex items-center gap-4">
									<div className="p-2 bg-green-100 dark:bg-green-900 rounded-full">
										<ShieldCheck className="h-5 w-5 text-green-600 dark:text-green-400" />
									</div>
									<div>
										<div className="flex items-center gap-2">
											<span className="font-medium">
												Authenticator App
											</span>
											<Badge
												variant="secondary"
												className="text-xs bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-400"
											>
												Enabled
											</Badge>
										</div>
										<p className="text-sm text-muted-foreground mt-1">
											{mfaStatus.recovery_codes_remaining}{" "}
											recovery codes remaining
										</p>
									</div>
								</div>
								<div className="flex gap-2">
									<Button
										variant="outline"
										size="sm"
										onClick={() => setShowRegenerateDialog(true)}
									>
										<Key className="h-4 w-4 mr-2" />
										Recovery Codes
									</Button>
									<Button
										variant="outline"
										size="sm"
										className="text-destructive hover:text-destructive"
										onClick={() => setShowRemoveDialog(true)}
									>
										<Trash2 className="h-4 w-4 mr-2" />
										Remove
									</Button>
								</div>
							</div>
						</div>
					)}

					{/* TOTP Setup Flow */}
					{totpStep === "setup" && totpSetup && (
						<div className="space-y-4">
							<Alert>
								<Info className="h-4 w-4" />
								<AlertDescription>
									Scan this QR code with your authenticator app
									(Google Authenticator, Authy, 1Password, etc.)
								</AlertDescription>
							</Alert>

							<div className="flex items-center justify-center p-4 bg-white rounded-lg">
								<QRCode
									data={totpSetup.qr_code_uri}
									size={200}
									alt="TOTP QR Code"
								/>
							</div>

							<div className="text-center space-y-2">
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

							<div className="flex gap-2">
								<Button
									variant="outline"
									className="flex-1"
									onClick={() => {
										setTotpStep("idle");
										setTotpSetup(null);
									}}
								>
									Cancel
								</Button>
								<Button
									className="flex-1"
									onClick={() => setTotpStep("verify")}
								>
									Continue
								</Button>
							</div>
						</div>
					)}

					{/* TOTP Verify Step */}
					{totpStep === "verify" && (
						<form onSubmit={handleVerifyTOTP} className="space-y-4">
							{totpError && (
								<Alert variant="destructive">
									<AlertTriangle className="h-4 w-4" />
									<AlertDescription>{totpError}</AlertDescription>
								</Alert>
							)}

							<div className="space-y-2">
								<Label htmlFor="totp-code">Verification Code</Label>
								<Input
									id="totp-code"
									type="text"
									inputMode="numeric"
									pattern="[0-9]*"
									placeholder="Enter 6-digit code"
									value={totpCode}
									onChange={(e) =>
										setTotpCode(e.target.value.replace(/\D/g, ""))
									}
									className="text-center text-lg tracking-widest"
									maxLength={6}
									autoFocus
								/>
								<p className="text-xs text-muted-foreground text-center">
									Enter the code from your authenticator app
								</p>
							</div>

							<div className="flex gap-2">
								<Button
									type="button"
									variant="outline"
									className="flex-1"
									onClick={() => setTotpStep("setup")}
									disabled={totpLoading}
								>
									Back
								</Button>
								<Button
									type="submit"
									className="flex-1"
									disabled={totpLoading || totpCode.length !== 6}
								>
									{totpLoading ? (
										<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									) : null}
									Verify
								</Button>
							</div>
						</form>
					)}

					{/* Recovery Codes Step */}
					{totpStep === "recovery-codes" && (
						<div className="space-y-4">
							<Alert>
								<AlertTriangle className="h-4 w-4" />
								<AlertTitle>Save your recovery codes</AlertTitle>
								<AlertDescription>
									These codes can be used to access your account if
									you lose your authenticator. Each code can only be
									used once. Store them securely.
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
								onClick={handleCompleteTOTPSetup}
								className="w-full"
								disabled={!recoveryCodesSaved}
							>
								<CheckCircle className="h-4 w-4 mr-2" />
								Done
							</Button>
						</div>
					)}
				</CardContent>
			</Card>

			{/* Passkeys Section */}
			<Card>
				<CardHeader>
					<div className="flex items-center justify-between">
						<div>
							<CardTitle className="flex items-center gap-2">
								<Fingerprint className="h-5 w-5" />
								Passkeys
							</CardTitle>
							<CardDescription className="mt-1">
								Sign in faster with Face ID, Touch ID, or
								security keys
							</CardDescription>
						</div>
						{support.supported && (
							<Button onClick={() => setShowAddDialog(true)}>
								<Plus className="h-4 w-4 mr-2" />
								Add Passkey
							</Button>
						)}
					</div>
				</CardHeader>
				<CardContent>
					{/* Unsupported browser */}
					{!support.supported && (
						<Alert variant="default">
							<AlertTriangle className="h-4 w-4" />
							<AlertTitle>Passkeys not supported</AlertTitle>
							<AlertDescription>
								Your browser doesn't support passkeys. Try using a
								modern browser like Chrome, Safari, or Edge on a
								device with biometric authentication.
							</AlertDescription>
						</Alert>
					)}

					{support.supported && (
						<>
							{/* Info banner */}
							<Alert className="mb-6">
								<ShieldCheck className="h-4 w-4" />
								<AlertTitle>Passwordless login</AlertTitle>
								<AlertDescription>
									Passkeys let you sign in without a password using
									biometrics or your device PIN. They're phishing
									resistant and more secure than passwords.
								</AlertDescription>
							</Alert>

							{/* Loading state */}
							{isLoading && (
								<div className="space-y-3">
									<Skeleton className="h-16 w-full" />
									<Skeleton className="h-16 w-full" />
								</div>
							)}

							{/* Error state */}
							{isError && (
								<Alert variant="destructive">
									<AlertTriangle className="h-4 w-4" />
									<AlertDescription>
										Failed to load passkeys. Please try again.
									</AlertDescription>
								</Alert>
							)}

							{/* Empty state */}
							{!isLoading && !isError && passkeyCount === 0 && (
								<div className="text-center py-8 text-muted-foreground">
									<Fingerprint className="h-12 w-12 mx-auto mb-4 opacity-50" />
									<p className="font-medium">No passkeys yet</p>
									<p className="text-sm mt-1">
										Add a passkey to enable passwordless sign-in
									</p>
								</div>
							)}

							{/* Passkey list */}
							{!isLoading && !isError && passkeyCount > 0 && (
								<div className="space-y-3">
									{passkeys.map((passkey) => (
										<div
											key={passkey.id}
											className="flex items-center justify-between p-4 rounded-lg ring-1 ring-foreground/5 hover:bg-muted/50 transition-colors"
										>
											<div className="flex items-center gap-4">
												{getDeviceIcon(passkey.device_type)}
												<div>
													<div className="flex items-center gap-2">
														<span className="font-medium">
															{passkey.name}
														</span>
														{passkey.backed_up && (
															<Badge
																variant="secondary"
																className="text-xs"
															>
																<Cloud className="h-3 w-3 mr-1" />
																Synced
															</Badge>
														)}
													</div>
													<div className="flex items-center gap-3 text-sm text-muted-foreground mt-1">
														<span className="flex items-center gap-1">
															<Clock className="h-3 w-3" />
															Created{" "}
															{formatDistanceToNow(
																new Date(
																	passkey.created_at,
																),
																{ addSuffix: true },
															)}
														</span>
														{passkey.last_used_at && (
															<span>
																Last used{" "}
																{formatDistanceToNow(
																	new Date(
																		passkey.last_used_at,
																	),
																	{ addSuffix: true },
																)}
															</span>
														)}
													</div>
												</div>
											</div>
											<Button
												variant="ghost"
												size="icon"
												className="text-destructive hover:text-destructive hover:bg-destructive/10"
												onClick={() =>
													setPasskeyToDelete(passkey)
												}
											>
												<Trash2 className="h-4 w-4" />
											</Button>
										</div>
									))}
								</div>
							)}
						</>
					)}
				</CardContent>
			</Card>

			{/* Add Passkey Dialog */}
			<Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle className="flex items-center gap-2">
							<Fingerprint className="h-5 w-5" />
							Add Passkey
						</DialogTitle>
						<DialogDescription>
							Register a new passkey for passwordless sign-in.
							You'll be prompted to use Face ID, Touch ID, or your
							device PIN.
						</DialogDescription>
					</DialogHeader>

					<form
						onSubmit={(e) => {
							e.preventDefault();
							handleRegister();
						}}
					>
						<div className="space-y-4 py-4">
							<div className="space-y-2">
								<Label htmlFor="device-name">
									Device Name (optional)
								</Label>
								<Input
									id="device-name"
									placeholder='e.g., "MacBook Pro" or "iPhone"'
									value={deviceName}
									onChange={(e) =>
										setDeviceName(e.target.value)
									}
								/>
								<p className="text-xs text-muted-foreground">
									A friendly name to help you identify this
									passkey later
								</p>
							</div>

							<Alert>
								<Info className="h-4 w-4" />
								<AlertDescription className="text-sm">
									When you click "Register", your browser will
									prompt you to create a passkey. This uses
									your device's built-in security (Face ID,
									Touch ID, Windows Hello, etc.)
								</AlertDescription>
							</Alert>
						</div>

						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={() => setShowAddDialog(false)}
								disabled={isRegistering}
							>
								Cancel
							</Button>
							<Button type="submit" disabled={isRegistering}>
								{isRegistering ? (
									<>
										<Loader2 className="h-4 w-4 mr-2 animate-spin" />
										Registering...
									</>
								) : (
									<>
										<Fingerprint className="h-4 w-4 mr-2" />
										Register Passkey
									</>
								)}
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>

			{/* Delete Passkey Confirmation Dialog */}
			<AlertDialog
				open={!!passkeyToDelete}
				onOpenChange={() => setPasskeyToDelete(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Remove Passkey?</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to remove "
							{passkeyToDelete?.name}"? You won't be able to use
							this passkey to sign in anymore.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={isDeleting}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDelete}
							disabled={isDeleting}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{isDeleting ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Removing...
								</>
							) : (
								"Remove Passkey"
							)}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Remove MFA Dialog */}
			<Dialog open={showRemoveDialog} onOpenChange={setShowRemoveDialog}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Remove Two-Factor Authentication</DialogTitle>
						<DialogDescription>
							Enter your current authenticator code to remove
							two-factor authentication from your account.
						</DialogDescription>
					</DialogHeader>

					<div className="space-y-4 py-4">
						<div className="space-y-2">
							<Label htmlFor="remove-code">Authenticator Code</Label>
							<Input
								id="remove-code"
								type="text"
								inputMode="numeric"
								pattern="[0-9]*"
								placeholder="Enter 6-digit code"
								value={removeCode}
								onChange={(e) =>
									setRemoveCode(e.target.value.replace(/\D/g, ""))
								}
								className="text-center text-lg tracking-widest"
								maxLength={6}
							/>
						</div>

						<Alert variant="destructive">
							<AlertTriangle className="h-4 w-4" />
							<AlertDescription>
								Removing two-factor authentication will make your
								account less secure.
							</AlertDescription>
						</Alert>
					</div>

					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => {
								setShowRemoveDialog(false);
								setRemoveCode("");
							}}
							disabled={removeLoading}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							onClick={handleRemoveMFA}
							disabled={removeLoading || removeCode.length !== 6}
						>
							{removeLoading ? (
								<Loader2 className="h-4 w-4 mr-2 animate-spin" />
							) : null}
							Remove MFA
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Regenerate Recovery Codes Dialog */}
			<Dialog
				open={showRegenerateDialog}
				onOpenChange={setShowRegenerateDialog}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Regenerate Recovery Codes</DialogTitle>
						<DialogDescription>
							Enter your authenticator code to generate new recovery
							codes. Your old codes will no longer work.
						</DialogDescription>
					</DialogHeader>

					<div className="space-y-4 py-4">
						<div className="space-y-2">
							<Label htmlFor="regenerate-code">
								Authenticator Code
							</Label>
							<Input
								id="regenerate-code"
								type="text"
								inputMode="numeric"
								pattern="[0-9]*"
								placeholder="Enter 6-digit code"
								value={regenerateCode}
								onChange={(e) =>
									setRegenerateCode(
										e.target.value.replace(/\D/g, ""),
									)
								}
								className="text-center text-lg tracking-widest"
								maxLength={6}
							/>
						</div>
					</div>

					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => {
								setShowRegenerateDialog(false);
								setRegenerateCode("");
							}}
							disabled={regenerateLoading}
						>
							Cancel
						</Button>
						<Button
							onClick={handleRegenerateRecoveryCodes}
							disabled={
								regenerateLoading || regenerateCode.length !== 6
							}
						>
							{regenerateLoading ? (
								<Loader2 className="h-4 w-4 mr-2 animate-spin" />
							) : (
								<RefreshCw className="h-4 w-4 mr-2" />
							)}
							Regenerate
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
