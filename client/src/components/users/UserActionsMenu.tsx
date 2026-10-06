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
	status: string;
	isActive: boolean;
	isSelf: boolean;
	/** Invites, registration links, MFA reset, sign out, enable/disable (users.readwrite). */
	canSupport: boolean;
	/** Permanent deletion (users.lifecycle.readwrite). */
	canDelete: boolean;
	/** The caller can change at least one profile field (people can always edit their own name). */
	canEditProfile: boolean;
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
	/** Opens the profile editor; people can always edit their own name. */
	onEditProfile: () => void;
}

export function UserActionsMenu({
	label = "User actions",
	status,
	isActive,
	isSelf,
	canSupport,
	canDelete,
	canEditProfile,
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
	if (!canSupport && !canDelete && !isSelf) return null;

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
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
				{canEditProfile && (
					<>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onEditProfile}
							disabled={isProtected && !isSelf}
						>
							<Pencil className="h-4 w-4" />
							Edit profile
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
							{hasActiveInvite ? "Resend invite" : "Send invite"}
						</DropdownMenuItem>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onRegenerate}
							disabled={isProtected}
						>
							<RefreshCw className="h-4 w-4" />
							Generate registration link
						</DropdownMenuItem>
						<DropdownMenuItem
							className="min-h-11 lg:min-h-9"
							onClick={onCopyLink}
							disabled={isProtected}
						>
							<LinkIcon className="h-4 w-4" />
							Copy registration link
						</DropdownMenuItem>
						{hasActiveInvite && (
							<DropdownMenuItem
								variant="destructive"
								onClick={onRevoke}
								disabled={isProtected}
								className="min-h-11 lg:min-h-9"
							>
								<Ban className="h-4 w-4" />
								Revoke invite
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
							Sign out of all devices
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
