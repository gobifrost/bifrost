import { Link } from "react-router-dom";
import { BookOpen, CircleHelp, ExternalLink, Megaphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Logo } from "@/components/branding/Logo";
import { DiscordIcon } from "@/components/icons/DiscordIcon";
import { Github } from "@/components/icons/GithubIcon";
import { VersionMenuItem } from "./VersionMenuItem";

/** Header renders this menu only for platform admins. */
export function HelpMenu() {
	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="ghost"
					size="icon-lg"
					className="size-11 shrink-0"
					aria-label="Help"
					title="Help"
				>
					<CircleHelp className="size-4" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent
				align="end"
				collisionPadding={16}
				className="w-64 max-w-[calc(100vw-2rem)]"
			>
				<DropdownMenuLabel className="px-3 py-2">
					Help & Resources
				</DropdownMenuLabel>
				<DropdownMenuItem asChild className="min-h-11">
					<a
						href="https://gobifrost.com/docs/"
						target="_blank"
						rel="noreferrer"
					>
						<BookOpen aria-hidden="true" className="size-4" />
						Documentation
						<ExternalLink
							aria-hidden="true"
							className="ml-auto size-3 text-muted-foreground"
						/>
					</a>
				</DropdownMenuItem>
				{import.meta.env.DEV && (
					<DropdownMenuItem asChild className="min-h-11">
						<Link to="/whats-new">
							<Megaphone aria-hidden="true" className="size-4" />
							Release Notes
						</Link>
					</DropdownMenuItem>
				)}
				<DropdownMenuSeparator />
				<DropdownMenuItem asChild className="min-h-11">
					<a
						href="https://gobifrost.com"
						target="_blank"
						rel="noreferrer"
					>
						<Logo type="square" alt="" className="size-4" />
						Website
						<ExternalLink
							aria-hidden="true"
							className="ml-auto size-3 text-muted-foreground"
						/>
					</a>
				</DropdownMenuItem>
				<DropdownMenuItem asChild className="min-h-11">
					<a
						href="https://discord.gg/f7TCcWX2s"
						target="_blank"
						rel="noreferrer"
					>
						<DiscordIcon aria-hidden="true" className="size-4" />
						Discord
						<ExternalLink
							aria-hidden="true"
							className="ml-auto size-3 text-muted-foreground"
						/>
					</a>
				</DropdownMenuItem>
				<DropdownMenuItem asChild className="min-h-11">
					<a
						href="https://github.com/gobifrost/bifrost"
						target="_blank"
						rel="noreferrer"
					>
						<Github aria-hidden="true" className="size-4" />
						GitHub
						<ExternalLink
							aria-hidden="true"
							className="ml-auto size-3 text-muted-foreground"
						/>
					</a>
				</DropdownMenuItem>
				<DropdownMenuSeparator />
				<VersionMenuItem />
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
