import {
	Ban,
	Link as LinkIcon,
	LogOut,
	Mail,
	MoreVertical,
	Pencil,
	RefreshCw,
	Power,
	ShieldOff,
	Trash2,
} from "lucide-react";
import type { Ref } from "react";

import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export const PROTECTED_ACCOUNT_NOTICE =
	"Protected account — only a Platform Admin can change it";

interface Props {
	label?: string;
	/** The menu's trigger, for returning focus to it after a confirmation. */
	triggerRef?: Ref<HTMLButtonElement>;
	status: string;
	isActive: boolean;
	isSelf: boolean;
	/** Invites, registration links, MFA reset, sign out, enable/disable (users.readwrite). */
	canSupport: boolean;
	/** Permanent deletion (userlifecycle.readwrite). */
	canDelete: boolean;
	/** A protected user: the caller's actions show disabled, with why. */
	isProtected?: boolean;
	onResend: () => void;
	onRegenerate: () => void;
	onCopyLink: () => void;
	onRevoke: () => void;
	onResetMfa: () => void;
	onSignOut: () => void;
	onToggleActive: () => void;
	onDelete: () => void;
	/**
	 * Opens the profile editor, offered when the caller can change at least
	 * one profile field (people can always edit their own name). Left out
	 * where the profile is already on screen.
	 */
	onEditProfile?: () => void;
}

export function UserActionsMenu({
	label = "User actions",
	triggerRef,
	status,
	isActive,
	isSelf,
	canSupport,
	canDelete,
	isProtected = false,
	onResend,
	onRegenerate,
	onCopyLink,
	onRevoke,
	onResetMfa,
	onSignOut,
	onToggleActive,
	onDelete,
	onEditProfile,
}: Props) {
	const showInviteActions = canSupport && status !== "active";
	const hasActiveInvite = status === "pending" || status === "expired";
	// What the caller's roles don't grant is hidden; what protection blocks
	// shows disabled, with the reason.
	if (!canSupport && !canDelete && !onEditProfile) return null;

	return (
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
				onClick={(e) => e.stopPropagation()}
			>
				{isProtected && (
					<>
						<DropdownMenuLabel className="max-w-64 whitespace-normal text-xs font-normal text-muted-foreground">
							{PROTECTED_ACCOUNT_NOTICE}
						</DropdownMenuLabel>
						<DropdownMenuSeparator />
					</>
				)}
				{onEditProfile && (
					<>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onEditProfile}
							disabled={isProtected && !isSelf}
						>
							<Pencil className="h-4 w-4" />
							Edit Profile
						</DropdownMenuItem>
						{(showInviteActions || canSupport || canDelete) && (
							<DropdownMenuSeparator />
						)}
					</>
				)}
				{showInviteActions && (
					<>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onResend}
							disabled={isProtected}
						>
							<Mail className="h-4 w-4" />
							{hasActiveInvite ? "Resend Invite" : "Send Invite"}
						</DropdownMenuItem>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onRegenerate}
							disabled={isProtected}
						>
							<RefreshCw className="h-4 w-4" />
							Generate Registration Link
						</DropdownMenuItem>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onCopyLink}
							disabled={isProtected}
						>
							<LinkIcon className="h-4 w-4" />
							Copy Registration Link
						</DropdownMenuItem>
						{hasActiveInvite && (
							<DropdownMenuItem
								variant="destructive"
								onClick={onRevoke}
								disabled={isProtected}
								className="min-h-11 lg:min-h-9"
							>
								<Ban className="h-4 w-4" />
								Revoke Invite
							</DropdownMenuItem>
						)}
						<DropdownMenuSeparator />
					</>
				)}
				{canSupport && (
					<>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onResetMfa}
							disabled={isSelf || isProtected}
						>
							<ShieldOff className="h-4 w-4" />
							Reset MFA
						</DropdownMenuItem>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onSignOut}
							disabled={isSelf || isProtected}
						>
							<LogOut className="h-4 w-4" />
							Sign Out of All Devices
						</DropdownMenuItem>
						<DropdownMenuSeparator />
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onToggleActive}
							disabled={isSelf || isProtected}
						>
							<Power className="h-4 w-4" />
							{isActive ? "Disable" : "Enable"}
						</DropdownMenuItem>
					</>
				)}
				{canDelete && (
					<DropdownMenuItem
						variant="destructive"
						onClick={onDelete}
						disabled={isSelf || isProtected}
						className="min-h-11 lg:min-h-9"
					>
						<Trash2 className="h-4 w-4" />
						Delete
					</DropdownMenuItem>
				)}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
