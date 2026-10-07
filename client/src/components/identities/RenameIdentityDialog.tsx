import { useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { getErrorMessage } from "@/lib/api-error";
import { useRenameIdentity } from "@/services/identities";

function RenameIdentityDialogContent({
	identity,
	onOpenChange,
}: {
	identity: { id: string; name: string };
	onOpenChange: (open: boolean) => void;
}) {
	const rename = useRenameIdentity();
	const [name, setName] = useState(identity.name);
	const [error, setError] = useState<string | null>(null);
	const errorRef = useRef<HTMLDivElement>(null);
	const trimmed = name.trim();
	const busy = rename.isPending;
	useEffect(() => {
		if (error) errorRef.current?.focus();
	}, [error]);

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!trimmed || trimmed === identity.name || busy) return;
		setError(null);
		try {
			await rename.mutateAsync({
				params: { path: { identity_id: identity.id } },
				body: { name: trimmed },
			});
			toast.success("Identity renamed", {
				description: `It's now named ${trimmed}`,
			});
			onOpenChange(false);
		} catch (cause) {
			setError(
				getErrorMessage(cause, "The identity could not be renamed."),
			);
		}
	};

	return (
		<DialogContent
			onEscapeKeyDown={(event) => {
				if (busy) event.preventDefault();
			}}
			onInteractOutside={(event) => {
				if (busy) event.preventDefault();
			}}
			className="sm:max-w-md"
		>
			<form onSubmit={handleSubmit} className="space-y-4">
				<DialogHeader>
					<DialogTitle>Rename Identity</DialogTitle>
					<DialogDescription>
						Names are unique within the identity's organization.
					</DialogDescription>
				</DialogHeader>
				{error && (
					<Alert
						variant="destructive"
						ref={errorRef}
						tabIndex={-1}
						className="outline-none"
					>
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				)}
				<div className="space-y-2">
					<Label htmlFor="rename-identity-name">Name</Label>
					<Input
						id="rename-identity-name"
						value={name}
						onChange={(event) => setName(event.target.value)}
						maxLength={255}
						required
						autoFocus
					/>
				</div>
				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						className="h-11 sm:h-9"
						disabled={busy}
						onClick={() => onOpenChange(false)}
					>
						Cancel
					</Button>
					<Button
						type="submit"
						className="h-11 sm:h-9"
						disabled={!trimmed || trimmed === identity.name || busy}
					>
						{busy && (
							<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
						)}
						Rename
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

/** A custom identity's new name; a name its organization already has is refused (409) inline. */
export function RenameIdentityDialog({
	identity,
	open,
	onOpenChange,
}: {
	identity: { id: string; name: string };
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			{open && (
				<RenameIdentityDialogContent
					identity={identity}
					onOpenChange={onOpenChange}
				/>
			)}
		</Dialog>
	);
}
