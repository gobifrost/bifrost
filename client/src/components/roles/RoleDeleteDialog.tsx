import {
	AlertDialog,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";

export function RoleDeleteDialog({
	name,
	open,
	pending,
	error,
	onOpenChange,
	onDelete,
}: {
	name: string;
	open: boolean;
	pending: boolean;
	/** The server's reason the delete failed, e.g. the role is still
	 * someone's base role. */
	error: string | null;
	onOpenChange: (open: boolean) => void;
	onDelete: () => void;
}) {
	const returnFocus = useDialogReturnFocus();
	return (
		<AlertDialog
			open={open}
			onOpenChange={(value) => {
				if (!pending) onOpenChange(value);
			}}
		>
			<AlertDialogContent
				{...returnFocus}
				onEscapeKeyDown={(event) => {
					if (pending) event.preventDefault();
				}}
			>
				<AlertDialogHeader>
					<AlertDialogTitle>Delete role</AlertDialogTitle>
					<AlertDialogDescription className="[overflow-wrap:anywhere]">
						Delete “{name}”? This cannot be undone. It removes the
						role from every assigned user and resource.
					</AlertDialogDescription>
				</AlertDialogHeader>
				{error && (
					<p
						role="alert"
						className="rounded-[var(--bf-radius-control)] bg-[var(--bf-warning-soft)] p-3 text-sm"
					>
						{error}
					</p>
				)}
				<AlertDialogFooter>
					<AlertDialogCancel className="min-h-11" disabled={pending}>
						Cancel
					</AlertDialogCancel>
					<Button
						variant="destructive"
						className="min-h-11"
						disabled={pending}
						onClick={onDelete}
					>
						{pending ? "Deleting…" : "Delete role"}
					</Button>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
