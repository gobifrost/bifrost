import { LogOut, Settings } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { VersionMenuItem } from "./VersionMenuItem";

export function AccountMenuContent({
	name,
	email,
	initials,
	avatarUrl,
	onSettings,
	onLogout,
	showVersion = true,
}: {
	name: string;
	email: string;
	initials: string;
	avatarUrl?: string | null;
	onSettings: () => void;
	onLogout: () => void;
	showVersion?: boolean;
}) {
	return (
		<DropdownMenuContent
			align="end"
			collisionPadding={16}
			className="w-[min(20rem,calc(100vw-2rem))]"
		>
			<DropdownMenuLabel className="p-3">
				<div className="flex items-start gap-3">
					<Avatar className="size-10 shrink-0">
						<AvatarImage src={avatarUrl || undefined} />
						<AvatarFallback>{initials}</AvatarFallback>
					</Avatar>
					<div className="min-w-0 space-y-1 [overflow-wrap:anywhere]">
						<p className="text-sm font-medium">{name}</p>
						<p className="text-xs font-normal text-muted-foreground">
							{email}
						</p>
					</div>
				</div>
			</DropdownMenuLabel>
			<DropdownMenuSeparator />
			<DropdownMenuItem className="min-h-11" onClick={onSettings}>
				<Settings aria-hidden="true" className="size-4" />
				Settings
			</DropdownMenuItem>
			<DropdownMenuSeparator />
			<DropdownMenuItem
				variant="destructive"
				className="min-h-11"
				onClick={onLogout}
			>
				<LogOut aria-hidden="true" className="size-4" />
				Log out
			</DropdownMenuItem>
			{showVersion && (
				<>
					<DropdownMenuSeparator />
					<VersionMenuItem />
				</>
			)}
		</DropdownMenuContent>
	);
}
