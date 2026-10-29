import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { BulkDialogFrame } from "@/components/users/BulkUserDialogs";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";
import { useDeleteIdentity, type Identity } from "@/services/identities";

type BulkUserResponse = components["schemas"]["BulkUserResponse"];

function identities(count: number): string {
	return `${count} ${count === 1 ? "Identity" : "Identities"}`;
}

function BulkDeleteIdentitiesDialogInner({
	onOpenChange,
	deletable,
	skipped,
	onPartialFailure,
	onSuccess,
}: {
	onOpenChange: (open: boolean) => void;
	deletable: Identity[];
	skipped: number;
	onPartialFailure: (
		result: BulkUserResponse,
		identities: Identity[],
	) => void;
	onSuccess: () => void;
}) {
	const deleteIdentity = useDeleteIdentity();
	const [busy, setBusy] = useState(false);
	const submitBusy = useRef(false);
	const title = `Delete ${identities(deletable.length)}`;

	// One DELETE per identity: each is refused on its own (409 while
	// workflows run as it), and the refusals are listed afterwards.
	const handleSubmit = async () => {
		if (submitBusy.current) return;
		submitBusy.current = true;
		setBusy(true);
		const result: BulkUserResponse = { succeeded: [], failed: [] };
		for (const identity of deletable) {
			try {
				await deleteIdentity.mutateAsync({
					params: { path: { identity_id: identity.id } },
				});
				result.succeeded.push(identity.id);
			} catch (cause) {
				result.failed.push({
					user_id: identity.id,
					reason: getErrorMessage(
						cause,
						"The identity could not be deleted.",
					),
				});
			}
		}
		submitBusy.current = false;
		setBusy(false);
		if (result.failed.length === 0)
			toast.success(`Deleted ${identities(result.succeeded.length)}`);
		else onPartialFailure(result, deletable);
		if (result.succeeded.length > 0) onSuccess();
		onOpenChange(false);
	};

	return (
		<BulkDialogFrame
			open
			onOpenChange={onOpenChange}
			busy={busy}
			compact
			title={title}
			description="Permanently deletes each selected custom identity. This cannot be undone. An identity that workflows run as can't be deleted until they run as another."
			footer={
				<DialogFooter className="gap-3">
					<Button
						variant="outline"
						disabled={busy}
						onClick={() => onOpenChange(false)}
						className="h-11"
					>
						Cancel
					</Button>
					<Button
						variant="destructive"
						onClick={() => void handleSubmit()}
						disabled={busy}
						className="h-11"
					>
						{busy ? "Deleting..." : title}
					</Button>
				</DialogFooter>
			}
		>
			{skipped > 0 && (
				<p className="text-sm text-muted-foreground">
					{skipped} default{" "}
					{skipped === 1 ? "identity" : "identities"} selected can't
					be deleted and will be skipped.
				</p>
			)}
		</BulkDialogFrame>
	);
}

/**
 * Deletes the selected custom identities; default identities in the
 * selection are skipped, with how many.
 */
export function BulkDeleteIdentitiesDialog({
	open,
	...props
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	deletable: Identity[];
	skipped: number;
	onPartialFailure: (
		result: BulkUserResponse,
		identities: Identity[],
	) => void;
	onSuccess: () => void;
}) {
	if (!open) return null;
	return <BulkDeleteIdentitiesDialogInner {...props} />;
}
