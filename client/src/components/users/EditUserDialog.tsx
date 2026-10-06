import { UserProfileForm, useUserProfileAbilities } from "./UserProfileForm";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Info, ShieldAlert, X } from "lucide-react";
import { useAuthorization } from "@/services/authorization";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];

interface EditUserDialogProps {
	user: User | undefined;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

// Extract dialog content to separate component for key-based remounting
function EditUserDialogContent({
	user,
	onOpenChange,
}: {
	user: User;
	onOpenChange: (open: boolean) => void;
}) {
	const [saving, setSaving] = useState(false);
	const authorization = useAuthorization();
	const { canSave } = useUserProfileAbilities(user);
	const close = () => onOpenChange(false);

	return (
		<DialogContent
			onEscapeKeyDown={(event) => {
				if (saving) event.preventDefault();
			}}
			onInteractOutside={(event) => {
				if (saving) event.preventDefault();
			}}
			showCloseButton={false}
			className="flex h-[100dvh] w-full max-w-none flex-col gap-0 overflow-hidden rounded-none border-border/70 p-0 shadow-xl motion-reduce:transition-none motion-reduce:animate-none sm:h-auto sm:max-h-[min(90dvh,48rem)] sm:w-[min(92vw,560px)] sm:rounded-[var(--bf-radius-feature)]"
		>
			<DialogHeader className="shrink-0 border-b border-border/70 px-4 pb-4 pt-[max(1rem,env(safe-area-inset-top))] text-left sm:px-6">
				<div className="flex items-start gap-3">
					<div className="min-w-0 flex-1">
						<DialogTitle className="flex flex-wrap items-center gap-2 text-pretty break-words">
							{canSave ? "Edit User" : "User Details"}
							{user.is_protected && (
								<Badge variant="warning">Protected</Badge>
							)}
						</DialogTitle>
						<DialogDescription className="mt-1.5 text-sm leading-5 [overflow-wrap:anywhere]">
							{user.name
								? `${user.name} · ${user.email}`
								: user.email}
						</DialogDescription>
					</div>
					<Button
						type="button"
						variant="ghost"
						size="icon-lg"
						onClick={close}
						aria-label="Close dialog"
						disabled={saving}
						className="h-11 w-11 shrink-0 rounded-[var(--bf-radius-control)] border border-border/70 bg-background/90 text-foreground hover:bg-muted motion-reduce:transition-none"
					>
						<X className="h-5 w-5" />
					</Button>
				</div>
			</DialogHeader>

			{user.is_protected && (
				<Alert className="mx-4 mt-4 w-auto shrink-0 sm:mx-6">
					{authorization.isPlatformAdmin ? (
						<Info className="h-4 w-4" />
					) : (
						<ShieldAlert className="h-4 w-4" />
					)}
					<AlertTitle>Protected Account</AlertTitle>
					<AlertDescription>
						{authorization.isPlatformAdmin
							? "This person holds privileged access. Only Platform Admins can change their profile, sign-in, or roles."
							: "This person holds privileged access, so only a Platform Admin can change them. You can still view their details."}
					</AlertDescription>
				</Alert>
			)}

			<UserProfileForm
				user={user}
				variant="dialog"
				onDone={close}
				onCancel={close}
				onBusyChange={setSaving}
			/>
		</DialogContent>
	);
}

export function EditUserDialog({
	user,
	open,
	onOpenChange,
}: EditUserDialogProps) {
	if (!user) return null;

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			{open && (
				<EditUserDialogContent
					user={user}
					onOpenChange={onOpenChange}
				/>
			)}
		</Dialog>
	);
}
