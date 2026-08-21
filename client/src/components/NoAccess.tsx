import { Home, LogOut, ShieldAlert } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";

interface NoAccessProps {
	title?: string;
	description?: string;
}

export function NoAccess({
	title = "Access Denied",
	description = "Your account does not have access to this area. Contact your administrator if you believe this is an error.",
}: NoAccessProps = {}) {
	const { logout } = useAuth();
	const navigate = useNavigate();

	return (
		<div className="min-h-screen bg-background flex items-center justify-center p-4">
			<Card className="max-w-md w-full">
				<CardContent className="flex flex-col items-center justify-center py-12 text-center">
					<ShieldAlert className="h-16 w-16 text-destructive" />
					<h1 className="mt-6 text-2xl font-bold tracking-tight">
						{title}
					</h1>
					<p className="mt-4 text-muted-foreground">
						{description}
					</p>
					<div className="mt-6 flex w-full flex-col gap-2">
						<Button
							onClick={() => navigate("/")}
							className="w-full"
						>
							<Home className="h-4 w-4" />
							Return to Dashboard
						</Button>
						<Button
							onClick={logout}
							variant="ghost"
							className="w-full"
						>
							<LogOut className="h-4 w-4" />
							Sign Out
						</Button>
					</div>
				</CardContent>
			</Card>
		</div>
	);
}
