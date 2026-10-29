import { useApplicationName } from "@/lib/applicationName";
/**
 * Device Authorization Page
 *
 * Lets a signed-in person confirm a CLI device code without approving it merely
 * by opening a verification link or entering the code.
 */

import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
	AlertCircle,
	CheckCircle,
	Home,
	Loader2,
	LogOut,
	Terminal,
	XCircle,
} from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import { Logo } from "@/components/branding/Logo";
import { getActiveToken } from "@/lib/auth-token";

type AuthorizationStep = "input" | "confirm" | "connected" | "denied" | "error";

const DEVICE_CODE_PATTERN = /^[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$/;

function normalizeDeviceCode(value: string): string | null {
	if (!DEVICE_CODE_PATTERN.test(value)) return null;

	const characters = value.replace("-", "").toUpperCase();
	return `${characters.slice(0, 4)}-${characters.slice(4)}`;
}

function getQueryDeviceCode(search: string): string | null {
	return normalizeDeviceCode(
		new URLSearchParams(search).get("user_code") ?? "",
	);
}

function getMalformedQueryError(search: string): string | null {
	const queryCode = new URLSearchParams(search).get("user_code");
	if (queryCode !== null && !normalizeDeviceCode(queryCode)) {
		return "The code in this link is invalid. Enter the 8-character code shown in your terminal.";
	}
	return null;
}

export function DevicePage() {
	const location = useLocation();
	return <DeviceAuthorization key={location.search} />;
}

function DeviceAuthorization() {
	const reducedMotion = useReducedMotion();
	const applicationName = useApplicationName();
	const outcomeRef = useRef<HTMLHeadingElement>(null);
	const navigate = useNavigate();
	const location = useLocation();
	const { isAuthenticated, isLoading: authLoading, logout, user } = useAuth();
	const initialCode = getQueryDeviceCode(location.search);

	const [step, setStep] = useState<AuthorizationStep>(
		initialCode ? "confirm" : "input",
	);
	const draftCode = (location.state as { deviceCodeDraft?: string } | null)
		?.deviceCodeDraft;
	const [userCode, setUserCode] = useState(
		initialCode ?? normalizeDeviceCode(draftCode ?? "") ?? "",
	);
	const [pendingDecision, setPendingDecision] = useState<boolean | null>(
		null,
	);
	const isSubmitting = pendingDecision !== null;
	const [error, setError] = useState<string | null>(
		getMalformedQueryError(location.search),
	);

	useEffect(() => {
		if (step !== "input") outcomeRef.current?.focus();
	}, [step, authLoading, isAuthenticated]);

	const formatUserCode = (value: string) => {
		const cleaned = value.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
		return cleaned.length > 4
			? `${cleaned.slice(0, 4)}-${cleaned.slice(4, 8)}`
			: cleaned;
	};

	const clearDeviceUrl = (draft?: string) => {
		navigate("/device", {
			replace: true,
			...(draft ? { state: { deviceCodeDraft: draft } } : {}),
		});
	};

	const handleUserCodeChange = (
		event: React.ChangeEvent<HTMLInputElement>,
	) => {
		setUserCode(formatUserCode(event.target.value));
		setError(null);
	};

	const handleContinue = (event: React.FormEvent) => {
		event.preventDefault();
		const code = normalizeDeviceCode(userCode);
		if (!code) {
			setError("Enter the 8-character code shown in your terminal.");
			return;
		}

		setUserCode(code);
		setError(null);
		setStep("confirm");
		navigate(`/device?user_code=${encodeURIComponent(code)}`, {
			replace: true,
		});
	};

	const handleDecision = async (approved: boolean) => {
		if (isSubmitting) return;

		const code = normalizeDeviceCode(userCode);
		if (!code) {
			setError("Enter the 8-character code shown in your terminal.");
			setStep("input");
			return;
		}

		setError(null);
		setPendingDecision(approved);
		try {
			const accessToken = getActiveToken();
			if (!accessToken) throw new Error("Not authenticated");

			const response = await fetch(
				approved ? "/auth/device/authorize" : "/auth/device/deny",
				{
					method: "POST",
					headers: {
						"Content-Type": "application/json",
						Authorization: `Bearer ${accessToken}`,
					},
					body: JSON.stringify({ user_code: code }),
				},
			);

			if (response.status === 401) {
				navigate("/login", {
					state: {
						from: `${location.pathname}${location.search}${location.hash}`,
					},
					replace: true,
				});
				return;
			}
			if (response.status === 404) {
				setError(
					"This device code is invalid or has expired. Start again from your terminal.",
				);
				setStep("error");
				return;
			}
			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				throw new Error(
					errorData.detail ||
						"We could not update this CLI connection.",
				);
			}

			setStep(approved ? "connected" : "denied");
		} catch (err) {
			setError(
				err instanceof Error
					? err.message
					: "We could not update this CLI connection.",
			);
			setStep("error");
		} finally {
			setPendingDecision(null);
		}
	};

	const handleEditCode = () => {
		setError(null);
		setStep("input");
		clearDeviceUrl(userCode);
	};

	const handleReset = () => {
		setUserCode("");
		setError(null);
		setStep("input");
		clearDeviceUrl();
	};

	if (authLoading) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-background">
				<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
			</div>
		);
	}

	if (!isAuthenticated) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-background">
				<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
			</div>
		);
	}

	const description = {
		input: "Enter the code shown in your terminal",
		confirm: "Review this CLI connection before you continue",
		connected: "Your CLI can continue in the terminal",
		denied: "This CLI connection was not approved",
		error: "We could not complete this CLI connection",
	}[step];

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
					<CardHeader className="space-y-4 pb-2 text-center">
						<div className="flex justify-center">
							<div className="relative">
								<Logo
									type="square"
									className="h-16 w-16"
									alt={applicationName}
								/>
								<div className="absolute -bottom-1 -right-1 flex h-6 w-6 items-center justify-center rounded-full bg-primary">
									<Terminal className="h-3 w-3 text-primary-foreground" />
								</div>
							</div>
						</div>
						<div className="space-y-1">
							<h1
								ref={
									step === "confirm" ? outcomeRef : undefined
								}
								tabIndex={step === "confirm" ? -1 : undefined}
								className="font-display text-2xl font-semibold tracking-tight outline-none"
							>
								{step === "confirm"
									? "Connect Bifrost CLI"
									: "Bifrost CLI"}
							</h1>
							<CardDescription className="text-base">
								{description}
							</CardDescription>
						</div>
					</CardHeader>
					<CardContent>
						{step === "input" && (
							<form
								className="space-y-4"
								onSubmit={handleContinue}
							>
								<Alert role="note">
									<Terminal className="h-4 w-4" />
									<AlertDescription>
										Signed in as{" "}
										<strong className="[overflow-wrap:anywhere]">
											{user?.email}
										</strong>
									</AlertDescription>
								</Alert>
								<div className="space-y-2">
									<Label htmlFor="userCode">
										Device Code
									</Label>
									<Input
										id="userCode"
										type="text"
										placeholder="XXXX-YYYY"
										value={userCode}
										onChange={handleUserCodeChange}
										className="h-12 text-center font-mono text-2xl tracking-widest"
										aria-describedby="device-code-help"
										maxLength={9}
										autoFocus
										autoComplete="off"
									/>
									<p
										id="device-code-help"
										className="text-center text-xs text-muted-foreground"
									>
										Enter the 8-character code from your
										CLI.
									</p>
								</div>
								{error && <ErrorAlert message={error} />}
								<Button
									type="submit"
									className="min-h-11 w-full"
									disabled={!normalizeDeviceCode(userCode)}
								>
									Continue
								</Button>
								<AccountActions
									onHome={() => navigate("/")}
									onLogout={logout}
								/>
							</form>
						)}

						{step === "confirm" && (
							<div className="space-y-4" aria-busy={isSubmitting}>
								<Alert role="note">
									<AlertDescription>
										Signed in as{" "}
										<strong className="[overflow-wrap:anywhere]">
											{user?.email}
										</strong>
									</AlertDescription>
								</Alert>
								<div className="space-y-2 text-center">
									<p
										className="font-mono text-3xl font-semibold tracking-[0.18em]"
										aria-label={`Device code ${userCode}`}
									>
										{userCode}
									</p>
									<p className="text-sm text-muted-foreground">
										Verify this matches your terminal. Only
										connect if you started this CLI login.
									</p>
								</div>
								<p className="text-sm text-muted-foreground">
									The CLI can access data and perform actions
									allowed by your account permissions. It
									stores credentials on the computer running
									the CLI to keep you signed in.
								</p>
								{error && <ErrorAlert message={error} />}
								<div className="space-y-2">
									<Button
										className="min-h-11 w-full"
										aria-busy={pendingDecision === true}
										onClick={() => handleDecision(true)}
										disabled={isSubmitting}
									>
										{pendingDecision === true && (
											<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
										)}
										Connect CLI
									</Button>
									<Button
										variant="outline"
										className="min-h-11 w-full"
										aria-busy={pendingDecision === false}
										onClick={() => handleDecision(false)}
										disabled={isSubmitting}
									>
										{pendingDecision === false && (
											<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
										)}
										Deny
									</Button>
									<Button
										variant="ghost"
										className="min-h-11 w-full"
										onClick={handleEditCode}
										disabled={isSubmitting}
									>
										Edit Code
									</Button>
								</div>
								<AccountActions
									onHome={() => navigate("/")}
									onLogout={logout}
								/>
							</div>
						)}

						{(step === "connected" ||
							step === "denied" ||
							step === "error") && (
							<Outcome
								kind={step}
								error={error}
								onReset={handleReset}
								onHome={() => navigate("/")}
								onLogout={logout}
								outcomeRef={outcomeRef}
							/>
						)}
					</CardContent>
				</Card>
			</motion.div>
		</div>
	);
}

function ErrorAlert({ message }: { message: string }) {
	return (
		<Alert variant="destructive" role="alert">
			<AlertCircle className="h-4 w-4" />
			<AlertDescription>{message}</AlertDescription>
		</Alert>
	);
}

function AccountActions({
	onHome,
	onLogout,
}: {
	onHome: () => void;
	onLogout: () => void;
}) {
	return (
		<div className="grid gap-2 sm:grid-cols-2">
			<Button
				type="button"
				className="min-h-11"
				variant="outline"
				onClick={onHome}
			>
				<Home className="h-4 w-4" />
				Return to Dashboard
			</Button>
			<Button
				type="button"
				className="min-h-11"
				variant="ghost"
				onClick={onLogout}
			>
				<LogOut className="h-4 w-4" />
				Sign Out
			</Button>
		</div>
	);
}

function Outcome({
	kind,
	error,
	onReset,
	onHome,
	onLogout,
	outcomeRef,
}: {
	kind: "connected" | "denied" | "error";
	error: string | null;
	onReset: () => void;
	onHome: () => void;
	onLogout: () => void;
	outcomeRef: React.RefObject<HTMLHeadingElement | null>;
}) {
	const isConnected = kind === "connected";
	const isDenied = kind === "denied";
	const Icon = isConnected ? CheckCircle : isDenied ? XCircle : AlertCircle;
	const title = isConnected
		? "CLI Connected"
		: isDenied
			? "CLI Connection Denied"
			: "CLI Connection Failed";
	const message = isConnected
		? "Return to your terminal to continue. You can close this window."
		: isDenied
			? "Return to your terminal. The login was denied, so no CLI credentials were issued."
			: "Return to your terminal and start the login again, then enter the new code here.";

	return (
		<div className="space-y-4 text-center">
			<div className="flex justify-center">
				<div
					className={`flex h-16 w-16 items-center justify-center rounded-full ${isConnected ? "bg-[color-mix(in_srgb,var(--bf-success)_12%,transparent)]" : "bg-destructive/10"}`}
				>
					<Icon
						className={`h-8 w-8 ${isConnected ? "text-[var(--bf-success)]" : "text-destructive"}`}
					/>
				</div>
			</div>
			<div className="space-y-2">
				<h2
					ref={outcomeRef}
					tabIndex={-1}
					className="font-display text-lg font-semibold outline-none"
				>
					{title}
				</h2>
				{error && <ErrorAlert message={error} />}
				<p className="text-sm text-muted-foreground">{message}</p>
			</div>
			<div className="space-y-2">
				{!isConnected && (
					<Button className="min-h-11 w-full" onClick={onReset}>
						Try Another Code
					</Button>
				)}
				<Button
					variant={isConnected ? "default" : "outline"}
					className="min-h-11 w-full"
					onClick={onHome}
				>
					<Home className="h-4 w-4" />
					Return to Dashboard
				</Button>
				<Button
					variant="ghost"
					className="min-h-11 w-full"
					onClick={onLogout}
				>
					<LogOut className="h-4 w-4" />
					Sign Out
				</Button>
			</div>
		</div>
	);
}

export default DevicePage;
