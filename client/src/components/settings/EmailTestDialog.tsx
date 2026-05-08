import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	currentUserEmail: string;
	onTest: (recipient: string) => void;
	isPending: boolean;
}

/**
 * Test-send dialog for the email workflow.
 *
 * Pre-fills the recipient with the current user's address — admins can change
 * it before sending. The actual API call is the parent's responsibility (we
 * don't import api-client here so this stays trivially testable).
 */
export function EmailTestDialog({
	open,
	onOpenChange,
	currentUserEmail,
	onTest,
	isPending,
}: Props) {
	const [recipient, setRecipient] = useState(currentUserEmail);

	useEffect(() => {
		if (open) setRecipient(currentUserEmail);
	}, [open, currentUserEmail]);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Test email workflow</DialogTitle>
					<DialogDescription>
						Validates the workflow signature and sends a real test
						message to the recipient below.
					</DialogDescription>
				</DialogHeader>
				<div className="space-y-2">
					<Label htmlFor="test-recipient">Recipient</Label>
					<Input
						id="test-recipient"
						type="email"
						value={recipient}
						onChange={(e) => setRecipient(e.target.value)}
						disabled={isPending}
					/>
				</div>
				<DialogFooter>
					<Button
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={isPending}
					>
						Cancel
					</Button>
					<Button
						onClick={() => onTest(recipient)}
						disabled={isPending || !recipient}
					>
						{isPending ? "Sending..." : "Send test"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
