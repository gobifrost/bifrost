import { Loader2 } from "lucide-react";
import { motion } from "framer-motion";

import { Logo } from "@/components/branding/Logo";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Full-card "we're finishing up" state for auth flows.
 *
 * Shown in the window between a credential/passkey/password success and the
 * redirect, where the server is still resolving the session. Without it the
 * page sits on the form (with only a small button spinner) for a beat on a
 * slow connection, which reads as "nothing happened".
 */
export function AuthTransition({ message }: { message: string }) {
	return (
		<div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-primary/5 p-4">
			<Card className="w-full max-w-md border-primary/10 shadow-xl shadow-primary/5">
				<CardContent className="flex flex-col items-center gap-4 py-12 text-center">
					<motion.div
						initial={{ scale: 0.8, opacity: 0 }}
						animate={{ scale: 1, opacity: 1 }}
						transition={{ duration: 0.3 }}
						className="flex justify-center"
					>
						<Logo type="square" className="h-16 w-16" alt="Bifrost" />
					</motion.div>
					<Loader2 className="h-6 w-6 animate-spin text-primary" />
					<p className="text-sm text-muted-foreground">{message}</p>
				</CardContent>
			</Card>
		</div>
	);
}
