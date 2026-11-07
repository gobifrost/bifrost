import { Link } from "react-router-dom";
import { ShieldCheck } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export function ZeroTrustAutomation() {
	return (
		<div className="max-w-3xl space-y-6">
			<div className="space-y-2">
				<h2 className="text-lg font-semibold">Zero Trust Automation</h2>
				<p className="text-sm text-muted-foreground">
					Review existing workflow access before changing the platform
					default. Inherited workflow access can change immediately.
				</p>
			</div>
			<Alert>
				<ShieldCheck className="size-4" />
				<AlertDescription>
					The conversion wizard analyzes workflow source and observed
					operations. It never executes a workflow.
				</AlertDescription>
			</Alert>
			<Button asChild>
				<Link to="/workflows/convert?platform=true">
					Review platform readiness
				</Link>
			</Button>
		</div>
	);
}
