import { useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { KeyRound, Lock, Loader2 } from "lucide-react";
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
import { apiClient } from "@/lib/api-client";

export function Register() {
	const [params] = useSearchParams();
	const token = params.get("token") ?? "";
	const [password, setPassword] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [pending, setPending] = useState(false);
	const nav = useNavigate();

	if (!token) {
		return (
			<div className="min-h-screen flex items-center justify-center p-8">
				<p className="text-muted-foreground">Missing invite token.</p>
			</div>
		);
	}

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		setPending(true);
		setError(null);
		try {
			const { error: apiError } = await apiClient.POST(
				"/auth/register-from-invite",
				{ body: { token, password } },
			);
			if (apiError) throw apiError;
			nav("/login", { state: { message: "Registration complete! Please sign in." } });
		} catch (e: unknown) {
			const msg = e instanceof Error
				? e.message
				: (e as { detail?: string })?.detail ?? "Registration failed";
			setError(msg);
		} finally {
			setPending(false);
		}
	};

	return (
		<div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-primary/5 p-4">
			<Card className="w-full max-w-md border-primary/10 shadow-xl shadow-primary/5">
				<CardHeader className="text-center space-y-2 pb-2">
					<h1 className="text-2xl font-bold tracking-tight">
						Complete your registration
					</h1>
					<CardDescription>
						Set a password to finish creating your account.
					</CardDescription>
				</CardHeader>
				<CardContent>
					{error && (
						<Alert variant="destructive" className="mb-4">
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					<form onSubmit={handleSubmit} className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="reg-password">Password</Label>
							<div className="relative">
								<Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
								<Input
									id="reg-password"
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
							disabled={pending || !password}
						>
							{pending ? (
								<Loader2 className="h-4 w-4 animate-spin mr-2" />
							) : (
								<KeyRound className="h-4 w-4 mr-2" />
							)}
							Create account
						</Button>
					</form>
				</CardContent>
			</Card>
		</div>
	);
}

export default Register;
