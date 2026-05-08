import {
	Ban,
	Link as LinkIcon,
	Mail,
	MoreVertical,
	RefreshCw,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface Props {
	userId: string;
	status: string;
	onResend: () => void;
	onRegenerate: () => void;
	onCopyLink: () => void;
	onRevoke: () => void;
}

/**
 * Invite-management dropdown for pending / expired / never-invited users.
 * Renders nothing for active users.
 */
export function InviteActionsMenu({
	status,
	onResend,
	onRegenerate,
	onCopyLink,
	onRevoke,
}: Props) {
	if (status === "active") return null;

	const hasActiveInvite = status === "pending" || status === "expired";

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button variant="ghost" size="icon" aria-label="Invite actions">
					<MoreVertical className="h-4 w-4" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent
				align="end"
				onClick={(e) => e.stopPropagation()}
			>
				<DropdownMenuItem onClick={onResend}>
					<Mail className="mr-2 h-4 w-4" />
					{hasActiveInvite ? "Resend invite" : "Send invite"}
				</DropdownMenuItem>
				<DropdownMenuItem onClick={onRegenerate}>
					<RefreshCw className="mr-2 h-4 w-4" />
					Regenerate link
				</DropdownMenuItem>
				<DropdownMenuItem onClick={onCopyLink}>
					<LinkIcon className="mr-2 h-4 w-4" />
					Copy registration link
				</DropdownMenuItem>
				{hasActiveInvite && (
					<DropdownMenuItem
						onClick={onRevoke}
						className="text-destructive"
					>
						<Ban className="mr-2 h-4 w-4" />
						Revoke invite
					</DropdownMenuItem>
				)}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
