import {
	Ban,
	Link as LinkIcon,
	Mail,
	MoreVertical,
	RefreshCw,
	Power,
	Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface Props {
	status: string;
	isActive: boolean;
	isSelf: boolean;
	onResend: () => void;
	onRegenerate: () => void;
	onCopyLink: () => void;
	onRevoke: () => void;
	onToggleActive: () => void;
	onDelete: () => void;
}

export function UserActionsMenu({
	status,
	isActive,
	isSelf,
	onResend,
	onRegenerate,
	onCopyLink,
	onRevoke,
	onToggleActive,
	onDelete,
}: Props) {
	const showInviteActions = status !== "active";
	const hasActiveInvite = status === "pending" || status === "expired";

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button variant="ghost" size="icon" aria-label="User actions">
					<MoreVertical className="h-4 w-4" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent
				align="end"
				onClick={(e) => e.stopPropagation()}
			>
				{showInviteActions && (
					<>
						<DropdownMenuItem onClick={onResend}>
							<Mail className="mr-2 h-4 w-4" />
							{hasActiveInvite ? "Resend invite" : "Send invite"}
						</DropdownMenuItem>
						<DropdownMenuItem onClick={onRegenerate}>
							<RefreshCw className="mr-2 h-4 w-4" />
							Generate registration link
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
						<DropdownMenuSeparator />
					</>
				)}
				<DropdownMenuItem onClick={onToggleActive} disabled={isSelf}>
					<Power className="mr-2 h-4 w-4" />
					{isActive ? "Disable user" : "Enable user"}
				</DropdownMenuItem>
				<DropdownMenuItem
					onClick={onDelete}
					disabled={isSelf}
					className="text-destructive"
				>
					<Trash2 className="mr-2 h-4 w-4" />
					Delete permanently
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
