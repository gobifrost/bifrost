import { useEffect, useRef, useState, type ReactNode } from "react";
import { LogOut, Settings } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { copyToClipboard } from "@/lib/clipboard";
import { APP_VERSION } from "@/lib/version";

export function AccountMenuContent({
	name,
	email,
	initials,
	avatarUrl,
	onSettings,
	onLogout,
	updatesMenuItem,
}: {
	name: string;
	email: string;
	initials: string;
	avatarUrl?: string | null;
	onSettings: () => void;
	onLogout: () => void;
	updatesMenuItem?: ReactNode;
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
			{updatesMenuItem && <DropdownMenuSeparator />}
			{updatesMenuItem}
			<DropdownMenuSeparator />
			<DropdownMenuItem
				variant="destructive"
				className="min-h-11"
				onClick={onLogout}
			>
				<LogOut aria-hidden="true" className="size-4" />
				Log out
			</DropdownMenuItem>
			<DropdownMenuSeparator />
			<VersionMenuItem />
		</DropdownMenuContent>
	);
}

function VersionMenuItem() {
	const [status, setStatus] = useState<
		"idle" | "copying" | "copied" | "failed"
	>("idle");
	const mounted = useRef(true);
	useEffect(() => {
		mounted.current = true;
		return () => {
			mounted.current = false;
		};
	}, []);
	useEffect(() => {
		if (status !== "copied") return;
		const timer = setTimeout(() => setStatus("idle"), 1500);
		return () => clearTimeout(timer);
	}, [status]);
	return (
		<DropdownMenuItem
			className="min-h-11 justify-center whitespace-normal text-center font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]"
			aria-label={`Copy version ${APP_VERSION}`}
			disabled={status === "copying"}
			onSelect={(event) => {
				event.preventDefault();
				setStatus("copying");
				void copyToClipboard(APP_VERSION).then((success) => {
					if (mounted.current)
						setStatus(success ? "copied" : "failed");
				});
			}}
		>
			<span aria-live="polite">
				{status === "copying"
					? "Copying…"
					: status === "copied"
						? "Copied!"
						: status === "failed"
							? "Copy failed. Try again."
							: APP_VERSION}
			</span>
		</DropdownMenuItem>
	);
}
