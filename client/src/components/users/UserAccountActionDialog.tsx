import {
	useEffect,
	useRef,
	useState,
	type ReactNode,
	type RefObject,
} from "react";
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
import { getErrorMessage } from "@/lib/api-error";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";

type Mode = "disable" | "delete" | "reset-mfa" | "sign-out";

const COPY: Record<
	Mode,
	{
		title: string;
		description: (name: string) => ReactNode;
		errorTitle: string;
		action: string;
		pending: string;
		destructive: boolean;
	}
> = {
	disable: {
		title: "Disable user",
		description: (name) => (
			<>
				Disable “{name}”? They will lose access to the platform. You can
				re-enable them later.
			</>
		),
		errorTitle: "User could not be disabled",
		action: "Disable",
		pending: "Disabling…",
		destructive: true,
	},
	delete: {
		title: "Permanently delete user",
		description: (name) => (
			<>
				Permanently delete “{name}”? This cannot be undone. The user and
				their associated data will be removed.
			</>
		),
		errorTitle: "User could not be deleted",
		action: "Permanently delete",
		pending: "Deleting…",
		destructive: true,
	},
	"reset-mfa": {
		title: "Reset MFA",
		description: (name) => (
			<>
				Removes {name}’s authenticator app, recovery codes, passkeys and
				remembered devices, and signs them out everywhere. They’ll set
				up MFA again at their next sign-in.
			</>
		),
		errorTitle: "MFA could not be reset",
		action: "Reset MFA",
		pending: "Resetting…",
		destructive: true,
	},
	"sign-out": {
		title: "Sign out of all devices",
		description: (name) => (
			<>
				Signs {name} out on every device. They can sign in again right
				away.
			</>
		),
		errorTitle: "User could not be signed out",
		action: "Sign out everywhere",
		pending: "Signing out…",
		destructive: false,
	},
};

export function UserAccountActionDialog({
	mode,
	name,
	onOpenChange,
	onConfirm,
	returnFocusRef,
}: {
	mode: Mode;
	name: string;
	onOpenChange: (open: boolean) => void;
	onConfirm: () => Promise<void>;
	returnFocusRef?: RefObject<HTMLElement | null>;
}) {
	const [pending, setPending] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const busy = useRef(false);
	const errorRef = useRef<HTMLDivElement>(null);
	const returnFocus = useDialogReturnFocus(returnFocusRef, true);
	const copy = COPY[mode];
	useEffect(() => {
		if (error) {
			errorRef.current?.focus();
			errorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [error]);
	async function confirm() {
		if (busy.current) return;
		busy.current = true;
		setPending(true);
		setError(null);
		try {
			await onConfirm();
		} catch (cause) {
			setError(
				getErrorMessage(
					cause,
					"The request could not be completed. Try again.",
				),
			);
		} finally {
			busy.current = false;
			setPending(false);
		}
	}
	return (
		<AlertDialog
			open
			onOpenChange={(open) => {
				if (!busy.current) onOpenChange(open);
			}}
		>
			<AlertDialogContent
				{...returnFocus}
				className="max-h-[90dvh] overflow-y-auto"
				onEscapeKeyDown={(event) => {
					if (busy.current) event.preventDefault();
				}}
			>
				<AlertDialogHeader>
					<AlertDialogTitle>{copy.title}</AlertDialogTitle>
					<AlertDialogDescription className="[overflow-wrap:anywhere]">
						{copy.description(name)}
					</AlertDialogDescription>
				</AlertDialogHeader>
				{error && (
					<div
						ref={errorRef}
						role="alert"
						tabIndex={-1}
						className="rounded-[var(--bf-radius-surface)] border border-destructive/30 bg-destructive/5 p-3 text-sm outline-none [overflow-wrap:anywhere]"
					>
						<p className="font-medium text-destructive">
							{copy.errorTitle}
						</p>
						<p className="mt-1 text-muted-foreground">{error}</p>
					</div>
				)}
				<AlertDialogFooter>
					<AlertDialogCancel disabled={pending} className="min-h-11">
						Cancel
					</AlertDialogCancel>
					<Button
						variant={copy.destructive ? "destructive" : "default"}
						disabled={pending}
						className="min-h-11"
						onClick={() => void confirm()}
					>
						{pending ? copy.pending : copy.action}
					</Button>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
