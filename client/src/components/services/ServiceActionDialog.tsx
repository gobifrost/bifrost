import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type {
	ServiceAction,
	ServiceListItem,
} from "@/services/services";

export interface PendingServiceAction {
	service: ServiceListItem;
	action: Extract<ServiceAction, "stop" | "restart" | "disable">;
}

interface ServiceActionDialogCopy {
	title: string;
	description: string;
	confirm: string;
	retry: string;
	pendingLabel: string;
}

function dialogCopy(pending: PendingServiceAction): ServiceActionDialogCopy {
	const name = pending.service.workflow_name;
	switch (pending.action) {
		case "stop":
			return {
				title: `Stop ${name}?`,
				description:
					"The running attempt shuts down gracefully and desired state becomes stopped. It will not restart until you start it again.",
				confirm: "Stop service",
				retry: "Retry stop",
				pendingLabel: "Stopping…",
			};
		case "restart":
			return {
				title: `Restart ${name}?`,
				description:
					"The running attempt stops and a fresh attempt starts with the current revision and policy.",
				confirm: "Restart service",
				retry: "Retry restart",
				pendingLabel: "Restarting…",
			};
		case "disable":
			return {
				title: `Disable ${name}?`,
				description:
					"The service stops now and will not start again until you re-enable it, even if startup is automatic.",
				confirm: "Disable service",
				retry: "Retry disable",
				pendingLabel: "Disabling…",
			};
	}
}

export interface ServiceActionDialogProps {
	pending: PendingServiceAction | null;
	actionPending: boolean;
	actionError: string | null;
	onConfirm: () => void;
	onOpenChange: (open: boolean) => void;
}

/**
 * Confirm dialog for destructive service control actions (2.1).
 *
 * Stop/Restart/Disable only — Start/Enable run directly. Follows the
 * Forms AlertDialog precedent (cancel disabled while pending, inline
 * error with retry label on failure).
 */
export function ServiceActionDialog({
	pending,
	actionPending,
	actionError,
	onConfirm,
	onOpenChange,
}: ServiceActionDialogProps) {
	const copy = pending ? dialogCopy(pending) : null;
	return (
		<AlertDialog
			open={pending !== null}
			onOpenChange={(open) => {
				if (!actionPending) onOpenChange(open);
			}}
		>
			<AlertDialogContent>
				<AlertDialogHeader>
					<AlertDialogTitle>{copy?.title ?? ""}</AlertDialogTitle>
					<AlertDialogDescription>
						{copy?.description ?? ""}
					</AlertDialogDescription>
				</AlertDialogHeader>
				{actionError && (
					<p role="alert" className="text-sm text-destructive">
						{actionError}
					</p>
				)}
				{actionPending && (
					<p role="status" className="sr-only">
						{copy?.pendingLabel ?? "Working…"}
					</p>
				)}
				<AlertDialogFooter>
					<AlertDialogCancel disabled={actionPending}>
						Cancel
					</AlertDialogCancel>
					<AlertDialogAction
						disabled={actionPending}
						onClick={(event) => {
							event.preventDefault();
							onConfirm();
						}}
						className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
					>
						{actionPending
							? (copy?.pendingLabel ?? "Working…")
							: actionError
								? (copy?.retry ?? "Retry")
								: (copy?.confirm ?? "Confirm")}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
