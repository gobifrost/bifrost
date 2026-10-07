import { useRef, useState } from "react";
import { ExternalLink, MoreVertical, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { RenameIdentityDialog } from "@/components/identities/RenameIdentityDialog";
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
import { useAuthorization } from "@/services/authorization";
import { useDeleteIdentity } from "@/services/identities";

/** What the menu needs of an identity, from the Identities list or its page. */
export interface IdentityActionsSubject {
	id: string;
	name: string;
	identity_kind?: string | null;
	organization_id?: string | null;
	/** Known on the identity's page; the server refuses a protected one either way. */
	is_protected?: boolean;
}

/** Whether the caller may rename and delete this identity: custom ones only,
 * with users.lifecycle.readwrite at its organization. */
export function canManageIdentity(
	authorization: Pick<ReturnType<typeof useAuthorization>, "canAt">,
	identity: Pick<IdentityActionsSubject, "identity_kind" | "organization_id">,
): boolean {
	return (
		identity.identity_kind === "custom" &&
		authorization.canAt(
			"users.lifecycle.readwrite",
			orgTarget(identity.organization_id),
		)
	);
}

/**
 * An identity's actions, on its row in Identities and on its page. Identities
 * don't sign in, so beyond opening one, only a custom identity can be renamed
 * or deleted; default identities keep their name and can't be deleted.
 */
export function IdentityActions({
	identity,
	label = `${identity.name} actions`,
	onOpen,
	onDeleted,
}: {
	identity: IdentityActionsSubject;
	/** The menu button's name; on a list, with the organization, since every default identity has the same name. */
	label?: string;
	/** Offered on the list, where the identity isn't open yet. */
	onOpen?: () => void;
	onDeleted?: () => void;
}) {
	const authorization = useAuthorization();
	const deleteIdentity = useDeleteIdentity();
	const triggerRef = useRef<HTMLButtonElement>(null);
	const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
	const canManage = canManageIdentity(authorization, identity);
	if (!onOpen && !canManage) return null;
	const isProtected =
		!!identity.is_protected && !authorization.isPlatformAdmin;

	return (
		<>
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button
						ref={triggerRef}
						variant="ghost"
						size="icon"
						aria-label={label}
						className="h-11 w-11 shrink-0 lg:h-9 lg:w-9"
					>
						<MoreVertical className="h-4 w-4" />
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent
					align="end"
					className="w-max min-w-40 whitespace-nowrap"
					onClick={(event) => event.stopPropagation()}
				>
					{canManage && isProtected && (
						<>
							<DropdownMenuLabel className="max-w-64 whitespace-normal text-xs font-normal text-muted-foreground">
								{PROTECTED_ACCOUNT_NOTICE}
							</DropdownMenuLabel>
							<DropdownMenuSeparator />
						</>
					)}
					{onOpen && (
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onOpen}
						>
							<ExternalLink className="h-4 w-4" />
							Open
						</DropdownMenuItem>
					)}
					{canManage && (
						<>
							{onOpen && <DropdownMenuSeparator />}
							<DropdownMenuItem
								disabled={isProtected}
								className="min-h-11 lg:min-h-9"
								onClick={() => setDialog("rename")}
							>
								<Pencil className="h-4 w-4" />
								Rename
							</DropdownMenuItem>
							<DropdownMenuItem
								variant="destructive"
								disabled={isProtected}
								className="min-h-11 lg:min-h-9"
								onClick={() => setDialog("delete")}
							>
								<Trash2 className="h-4 w-4" />
								Delete
							</DropdownMenuItem>
						</>
					)}
				</DropdownMenuContent>
			</DropdownMenu>
			<RenameIdentityDialog
				identity={identity}
				returnFocusRef={triggerRef}
				open={dialog === "rename"}
				onOpenChange={(open) => {
					if (!open) setDialog(null);
				}}
			/>
			{dialog === "delete" && (
				<UserAccountActionDialog
					mode="delete-identity"
					name={identity.name}
					returnFocusRef={triggerRef}
					onOpenChange={(open) => {
						if (!open) setDialog(null);
					}}
					onConfirm={async () => {
						await deleteIdentity.mutateAsync({
							params: { path: { identity_id: identity.id } },
						});
						toast.success("Identity deleted", {
							description: `${identity.name} has been permanently removed`,
						});
						setDialog(null);
						onDeleted?.();
					}}
				/>
			)}
		</>
	);
}
