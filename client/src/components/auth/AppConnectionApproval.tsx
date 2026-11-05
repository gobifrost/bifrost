import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2 } from "lucide-react";

interface AppConnectionApprovalProps {
	email?: string;
	clientName: string;
	returnAddress?: string;
	deviceCode?: string;
	pendingDecision: boolean | null;
	onDecision: (approved: boolean) => void;
	approveLabel?: string;
}

/** Shared consent content for the CLI and third-party MCP clients. */
export function AppConnectionApproval({
	email,
	clientName,
	returnAddress,
	deviceCode,
	pendingDecision,
	onDecision,
	approveLabel = "Allow",
}: AppConnectionApprovalProps) {
	const busy = pendingDecision !== null;
	return (
		<div className="space-y-4" aria-busy={busy}>
			<Alert role="note">
				<AlertDescription>
					Signed in as{" "}
					<strong className="[overflow-wrap:anywhere]">
						{email}
					</strong>
				</AlertDescription>
			</Alert>
			{deviceCode && (
				<div className="space-y-2 text-center">
					<p
						className="font-mono text-3xl font-semibold tracking-[0.18em]"
						aria-label={`Device code ${deviceCode}`}
					>
						{deviceCode}
					</p>
					<p className="text-sm text-muted-foreground">
						Verify this matches your terminal. Only connect if you
						started this CLI login.
					</p>
				</div>
			)}
			{returnAddress && (
				<div className="space-y-3">
					<p className="text-sm text-muted-foreground">
						{clientName === "Unnamed App"
							? "This app did not provide a name."
							: "The app supplied this name; Bifrost has not verified its publisher."}
					</p>
					<dl className="space-y-1">
						<dt className="text-sm font-medium">Return Address</dt>
						<dd className="break-all font-mono text-xs text-muted-foreground">
							{returnAddress}
						</dd>
					</dl>
				</div>
			)}
			<p className="text-sm text-muted-foreground">
				{clientName} can access data and perform actions allowed by your
				account permissions. It can store credentials to keep you signed
				in after this window closes.
			</p>
			<p className="text-sm text-muted-foreground">
				Only allow access if you started this connection and recognize
				the app. This approves your account only.
			</p>
			<div className="space-y-2">
				<Button
					className="min-h-11 w-full"
					onClick={() => onDecision(true)}
					disabled={busy}
					aria-busy={pendingDecision === true}
				>
					{pendingDecision === true && (
						<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
					)}
					{approveLabel}
				</Button>
				<Button
					variant="outline"
					className="min-h-11 w-full"
					onClick={() => onDecision(false)}
					disabled={busy}
					aria-busy={pendingDecision === false}
				>
					{pendingDecision === false && (
						<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
					)}
					Deny
				</Button>
			</div>
		</div>
	);
}
