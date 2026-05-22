import { useState } from "react";
import { Fingerprint, KeyRound, Lock, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
	email: string;
	onPasskeyRegister: () => Promise<void>;
	onPasswordRegister: (password: string) => Promise<void>;
	isPending: boolean;
	error: string | null;
}

export function AuthSetupSteps({
	onPasskeyRegister,
	onPasswordRegister,
	isPending,
	error,
}: Props) {
	const [showPassword, setShowPassword] = useState(false);
	const [password, setPassword] = useState("");

	const handlePasswordSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		await onPasswordRegister(password);
	};

	return (
		<div className="space-y-4">
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}

			{!showPassword ? (
				<div className="space-y-3">
					<Button
						type="button"
						className="w-full"
						disabled={isPending}
						onClick={onPasskeyRegister}
					>
						{isPending ? (
							<Loader2 className="h-4 w-4 animate-spin mr-2" />
						) : (
							<Fingerprint className="h-4 w-4 mr-2" />
						)}
						Set up passkey
					</Button>
					<Button
						type="button"
						variant="outline"
						className="w-full"
						disabled={isPending}
						onClick={() => setShowPassword(true)}
					>
						<KeyRound className="h-4 w-4 mr-2" />
						Use password instead
					</Button>
				</div>
			) : (
				<form onSubmit={handlePasswordSubmit} className="space-y-4">
					<div className="space-y-2">
						<Label htmlFor="auth-password">Password</Label>
						<div className="relative">
							<Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
							<Input
								id="auth-password"
								type="password"
								placeholder="At least 8 characters"
								value={password}
								onChange={(e) => setPassword(e.target.value)}
								className="pl-10"
								required
								minLength={8}
								autoFocus
							/>
						</div>
					</div>
					<Button
						type="submit"
						className="w-full"
						disabled={isPending || !password}
					>
						{isPending && (
							<Loader2 className="h-4 w-4 animate-spin mr-2" />
						)}
						Create account
					</Button>
					<Button
						type="button"
						variant="ghost"
						className="w-full"
						disabled={isPending}
						onClick={() => setShowPassword(false)}
					>
						Back
					</Button>
				</form>
			)}
		</div>
	);
}
