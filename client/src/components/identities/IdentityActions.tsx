import { useRef, useState } from "react";
import { MoreVertical, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { UserAccountActionDialog } from "@/components/users/UserAccountActionDialog";
import { PROTECTED_ACCOUNT_NOTICE } from "@/components/users/UserActionsMenu";
import { orgTarget } from "@/lib/authorization";
import type { components } from "@/lib/v1";
import { useAuthorization } from "@/services/authorization";
import { useDeleteIdentity } from "@/services/identities";

type User = components["schemas"]["UserPublic"];

/**
 * An identity's account actions on its page. Identities don't sign in, so
 * the only one is deleting a custom identity (users.lifecycle.readwrite at
 * its organization); default identities have none.
 */
export function IdentityActions({
	identity,
	onDeleted,
}: {
	identity: User;
	onDeleted: () => void;
}) {
	const authorization = useAuthorization();
	const deleteIdentity = useDeleteIdentity();
	const triggerRef = useRef<HTMLButtonElement>(null);
	const [confirming, setConfirming] = useState(false);
	if (
		identity.identity_kind !== "custom" ||
		!authorization.canAt(
			"users.lifecycle.readwrite",
			orgTarget(identity.organization_id),
		)
	)
		return null;
	const isProtected = identity.is_protected && !authorization.isPlatformAdmin;
	const name = identity.name || identity.email;

	return (
		<>
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button
						ref={triggerRef}
						variant="ghost"
						size="icon"
						aria-label={`${name} actions`}
						className="h-11 w-11 shrink-0 lg:h-9 lg:w-9"
					>
						<MoreVertical className="h-4 w-4" />
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent
					align="end"
					className="w-max min-w-40 whitespace-nowrap"
				>
					{isProtected && (
						<>
							<DropdownMenuLabel className="max-w-64 whitespace-normal text-xs font-normal text-muted-foreground">
								{PROTECTED_ACCOUNT_NOTICE}
							</DropdownMenuLabel>
							<DropdownMenuSeparator />
						</>
					)}
					<DropdownMenuItem
						variant="destructive"
						disabled={isProtected}
						className="min-h-11 lg:min-h-9"
						onClick={() => setConfirming(true)}
					>
						<Trash2 className="h-4 w-4" />
						Delete
					</DropdownMenuItem>
				</DropdownMenuContent>
			</DropdownMenu>
			{confirming && (
				<UserAccountActionDialog
					mode="delete-identity"
					name={name}
					returnFocusRef={triggerRef}
					onOpenChange={(open) => {
						if (!open) setConfirming(false);
					}}
					onConfirm={async () => {
						await deleteIdentity.mutateAsync({
							params: { path: { identity_id: identity.id } },
						});
						toast.success("Identity deleted", {
							description: `${name} has been permanently removed`,
						});
						setConfirming(false);
						onDeleted();
					}}
				/>
			)}
		</>
	);
}
