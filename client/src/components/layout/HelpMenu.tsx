import { AccountMenuSub } from "./AccountMenuSub";
import { Link } from "react-router-dom";
import { BookOpen, CircleHelp, ExternalLink, Megaphone } from "lucide-react";
import {
	DropdownMenuItem,
	DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { DiscordIcon } from "@/components/icons/DiscordIcon";
import { YouTubeIcon } from "@/components/icons/YouTubeIcon";
import { Github } from "@/components/icons/GithubIcon";

/** Header preserves the Global settings.read requirement for these resources. */
export function HelpMenu() {
	return (
		<AccountMenuSub label="Help" icon={<CircleHelp className="size-4" />}>
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
			<DropdownMenuItem asChild className="min-h-11">
				<Link to="/whats-new">
					<Megaphone aria-hidden="true" className="size-4" />
					Release Notes
				</Link>
			</DropdownMenuItem>
			<DropdownMenuSeparator />
			<DropdownMenuItem asChild className="min-h-11">
				<a
					href="https://gobifrost.com"
					target="_blank"
					rel="noreferrer"
				>
					<img src="/logo.svg" alt="" className="size-4" />
					Website
					<ExternalLink
						aria-hidden="true"
						className="ml-auto size-3 text-muted-foreground"
					/>
				</a>
			</DropdownMenuItem>
			<DropdownMenuItem asChild className="min-h-11">
				<a
					href="https://discord.gg/x84pft2YDa"
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
					href="https://www.youtube.com/@gobifrost"
					target="_blank"
					rel="noreferrer"
				>
					<YouTubeIcon aria-hidden="true" className="size-4" />
					YouTube
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
		</AccountMenuSub>
	);
}
