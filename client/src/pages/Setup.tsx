/**
 * Setup Wizard Page
 *
 * First-time setup for creating the initial admin user.
 * Only shown when no users exist in the system.
 *
 * Supports two registration methods:
 * 1. Passkey (preferred) - Passwordless via Face ID, Touch ID, etc.
 * 2. Password (fallback) - Traditional password + MFA setup
 */

import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { registerUser } from "@/services/auth";
import { setupWithPasskey } from "@/services/passkeys";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Loader2, Mail, User } from "lucide-react";
import { motion } from "framer-motion";
import { Logo } from "@/components/branding/Logo";
import { toast } from "sonner";
import { AuthSetupSteps } from "@/components/auth/AuthSetupSteps";

type SetupMode = "choose" | "auth";

export function Setup() {
	const navigate = useNavigate();
	const {
		needsSetup,
		isLoading: authLoading,
		checkAuthStatus,
		loginWithPasskey,
	} = useAuth();

	const [isLoading, setIsLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [mode, setMode] = useState<SetupMode>("choose");

	const [email, setEmail] = useState("");
	const [name, setName] = useState("");

	// Redirect if setup not needed
	useEffect(() => {
		if (!authLoading && !needsSetup) {
			navigate("/login");
		}
	}, [authLoading, needsSetup, navigate]);

	const handlePasskeySetup = async () => {
		setError(null);
		setIsLoading(true);
		try {
			await setupWithPasskey(email, name);
			await checkAuthStatus();
			await loginWithPasskey(email);
			toast.success("Account created successfully!");
			navigate("/");
		} catch (err) {
			setError(err instanceof Error ? err.message : "Passkey setup failed");
			setIsLoading(false);
		}
	};

	const handlePasswordSetup = async (password: string) => {
		setError(null);
		setIsLoading(true);
		try {
			await registerUser(email, password, name);
			await checkAuthStatus();
			navigate("/login", { state: { message: "Account created! Please sign in." } });
		} catch (err) {
			setError(err instanceof Error ? err.message : "Account creation failed");
			setIsLoading(false);
		}
	};

	if (authLoading) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-background">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	const renderChooseMode = () => (
		<div className="space-y-4">
			<div className="space-y-2">
				<Label htmlFor="name">Name</Label>
				<div className="relative">
					<User className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
					<Input
						id="name"
						type="text"
						placeholder="Your name"
						value={name}
						onChange={(e) => setName(e.target.value)}
						className="pl-10"
						autoFocus
					/>
				</div>
			</div>
			<div className="space-y-2">
				<Label htmlFor="email">Email</Label>
				<div className="relative">
					<Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
					<Input
						id="email"
						type="email"
						placeholder="admin@example.com"
						value={email}
						onChange={(e) => setEmail(e.target.value)}
						className="pl-10"
						required
					/>
				</div>
			</div>
			<Button
				type="button"
				className="w-full mt-2"
				disabled={!email}
				onClick={() => { setError(null); setMode("auth"); }}
			>
				Continue
			</Button>
		</div>
	);

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
							<CardTitle className="text-2xl font-bold tracking-tight">
								Welcome to Bifrost
							</CardTitle>
							<CardDescription className="text-base">
								{mode === "choose" &&
									"Create your admin account to get started"}
								{mode === "auth" &&
									"Choose how to secure your account"}
							</CardDescription>
						</div>
					</CardHeader>
					<CardContent>
						{mode === "choose" && renderChooseMode()}
						{mode === "auth" && (
							<AuthSetupSteps
								email={email}
								onPasskeyRegister={handlePasskeySetup}
								onPasswordRegister={handlePasswordSetup}
								isPending={isLoading}
								error={error}
							/>
						)}
					</CardContent>
				</Card>
			</motion.div>
		</div>
	);
}

export default Setup;
