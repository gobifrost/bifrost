/**
 * Device Authorization Page
 *
 * Allows users to authorize CLI access by entering a device code.
 * Accessed when CLI displays a user code and directs user to /device.
 */

import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
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
	Loader2,
	Terminal,
	CheckCircle,
	AlertCircle,
	Home,
	LogOut,
} from "lucide-react";
import { motion } from "framer-motion";
import { Logo } from "@/components/branding/Logo";
import { toast } from "sonner";

type AuthorizationStep = "input" | "authorized" | "error";

export function DevicePage() {
	const navigate = useNavigate();
	const location = useLocation();
	const {
		isAuthenticated,
		isLoading: authLoading,
		logout,
		user,
	} = useAuth();

	const [step, setStep] = useState<AuthorizationStep>("input");
	const [userCode, setUserCode] = useState("");
	const [isLoading, setIsLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// Redirect to login if not authenticated
	useEffect(() => {
		if (!authLoading && !isAuthenticated) {
			navigate("/login", {
				state: { from: location.pathname },
				replace: true,
			});
		}
	}, [authLoading, isAuthenticated, navigate, location.pathname]);

	// Format user code as XXXX-YYYY
	const formatUserCode = (value: string) => {
		// Remove any non-alphanumeric characters
		const cleaned = value.replace(/[^A-Za-z0-9]/g, "").toUpperCase();

		// Add hyphen after 4th character
		if (cleaned.length > 4) {
			return cleaned.slice(0, 4) + "-" + cleaned.slice(4, 8);
		}

		return cleaned;
	};

	const handleUserCodeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
		const formatted = formatUserCode(e.target.value);
		setUserCode(formatted);
		setError(null);
	};

	const handleAuthorize = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		setIsLoading(true);

		try {
			const accessToken = localStorage.getItem("bifrost_access_token");
			if (!accessToken) {
				throw new Error("Not authenticated");
			}

			const res = await fetch("/auth/device/authorize", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
					Authorization: `Bearer ${accessToken}`,
				},
				body: JSON.stringify({ user_code: userCode }),
			});

			if (res.status === 404) {
				setError(
					"Invalid or expired device code. Please check the code and try again.",
				);
				setStep("error");
				return;
			}

			if (res.status === 401) {
				// Session expired, redirect to login
				navigate("/login", {
					state: { from: location.pathname },
					replace: true,
				});
				return;
			}

			if (!res.ok) {
				const errorData = await res.json().catch(() => ({}));
				throw new Error(
					errorData.detail || "Failed to authorize device",
				);
			}

			// Success
			setStep("authorized");
			toast.success("CLI authorized successfully!");
		} catch (err) {
			const errorMessage =
				err instanceof Error
					? err.message
					: "Failed to authorize device";
			setError(errorMessage);
			setStep("error");
			toast.error(errorMessage);
		} finally {
			setIsLoading(false);
		}
	};

	const handleReset = () => {
		setStep("input");
		setUserCode("");
		setError(null);
	};

	const returnHome = () => {
		navigate("/");
	};

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
							<div className="relative">
								<Logo
									type="square"
									className="h-16 w-16"
									alt="Bifrost"
								/>
								<div className="absolute -bottom-1 -right-1 h-6 w-6 bg-primary rounded-full flex items-center justify-center">
									<Terminal className="h-3 w-3 text-primary-foreground" />
								</div>
							</div>
						</motion.div>
						<div className="space-y-1">
							<h1 className="text-2xl font-bold tracking-tight">
								Authorize CLI Access
							</h1>
							<CardDescription className="text-base">
								{step === "input" &&
									"Enter the code shown in your terminal"}
								{step === "authorized" &&
									"Device authorized successfully"}
								{step === "error" && "Authorization failed"}
							</CardDescription>
						</div>
					</CardHeader>
					<CardContent>
						{step === "input" && (
							<>
								<Alert className="mb-4">
									<Terminal className="h-4 w-4" />
									<AlertDescription>
										Authorizing as{" "}
										<strong>{user?.email}</strong>
									</AlertDescription>
								</Alert>

								<form
									onSubmit={handleAuthorize}
									className="space-y-4"
								>
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
											className="text-center text-2xl tracking-widest font-mono"
											maxLength={9} // XXXX-YYYY = 9 chars
											autoFocus
											autoComplete="off"
										/>
										<p className="text-xs text-muted-foreground text-center">
											Enter the 8-character code from your
											CLI
										</p>
									</div>

									{error && (
										<Alert variant="destructive">
											<AlertCircle className="h-4 w-4" />
											<AlertDescription>
												{error}
											</AlertDescription>
										</Alert>
									)}

									<Button
										type="submit"
										className="w-full"
										disabled={
											isLoading ||
											userCode.length !== 9 ||
											!userCode.includes("-")
										}
									>
										{isLoading ? (
											<Loader2 className="h-4 w-4 animate-spin mr-2" />
										) : (
											<Terminal className="h-4 w-4 mr-2" />
										)}
										Authorize Device
									</Button>

									<div className="grid gap-2 sm:grid-cols-2">
										<Button
											type="button"
											variant="outline"
											onClick={returnHome}
										>
											<Home className="h-4 w-4" />
											Return to Dashboard
										</Button>
										<Button
											type="button"
											variant="ghost"
											onClick={logout}
										>
											<LogOut className="h-4 w-4" />
											Sign Out
										</Button>
									</div>
								</form>
							</>
						)}

						{step === "authorized" && (
							<motion.div
								initial={{ opacity: 0, scale: 0.9 }}
								animate={{ opacity: 1, scale: 1 }}
								transition={{ duration: 0.3 }}
								className="space-y-4 text-center"
							>
								<div className="flex justify-center">
									<div className="h-16 w-16 bg-green-100 dark:bg-green-900/20 rounded-full flex items-center justify-center">
										<CheckCircle className="h-8 w-8 text-green-600 dark:text-green-400" />
									</div>
								</div>

								<div className="space-y-2">
									<h3 className="text-lg font-semibold text-green-900 dark:text-green-100">
										CLI Authorized!
									</h3>
									<p className="text-sm text-muted-foreground">
										You can now return to your terminal and
										continue working. This window can be
										closed.
									</p>
								</div>

								<div className="space-y-2">
									<Button
										className="w-full"
										onClick={returnHome}
									>
										<Home className="h-4 w-4" />
										Return to Dashboard
									</Button>
									<Button
										variant="outline"
										className="w-full"
										onClick={handleReset}
									>
										<Terminal className="h-4 w-4" />
										Authorize Another Device
									</Button>
									<Button
										variant="ghost"
										className="w-full"
										onClick={logout}
									>
										<LogOut className="h-4 w-4" />
										Sign Out
									</Button>
								</div>
							</motion.div>
						)}

						{step === "error" && (
							<motion.div
								initial={{ opacity: 0, scale: 0.9 }}
								animate={{ opacity: 1, scale: 1 }}
								transition={{ duration: 0.3 }}
								className="space-y-4 text-center"
							>
								<div className="flex justify-center">
									<div className="h-16 w-16 bg-red-100 dark:bg-red-900/20 rounded-full flex items-center justify-center">
										<AlertCircle className="h-8 w-8 text-red-600 dark:text-red-400" />
									</div>
								</div>

								<div className="space-y-2">
									<h3 className="text-lg font-semibold text-red-900 dark:text-red-100">
										Authorization Failed
									</h3>
									{error && (
										<Alert variant="destructive">
											<AlertDescription>
												{error}
											</AlertDescription>
										</Alert>
									)}
									<p className="text-sm text-muted-foreground">
										Please check the code and try again. If
										the problem persists, generate a new
										code from your CLI.
									</p>
								</div>

								<div className="space-y-2">
									<Button
										className="w-full"
										onClick={handleReset}
									>
										Try Again
									</Button>
									<Button
										variant="outline"
										className="w-full"
										onClick={returnHome}
									>
										<Home className="h-4 w-4" />
										Return to Dashboard
									</Button>
									<Button
										variant="ghost"
										className="w-full"
										onClick={logout}
									>
										<LogOut className="h-4 w-4" />
										Sign Out
									</Button>
								</div>
							</motion.div>
						)}
					</CardContent>
				</Card>
			</motion.div>
		</div>
	);
}

export default DevicePage;
