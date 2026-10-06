import { useState, type ReactNode } from "react";
import { ExternalLink, ImageOff } from "lucide-react";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import { Logo } from "@/components/branding/Logo";
import { DiscordIcon } from "@/components/icons/DiscordIcon";
import { Github } from "@/components/icons/GithubIcon";
import { Badge } from "@/components/ui/badge";
import type {
	ProductUpdateAsset,
	ProductUpdateEntry,
} from "@/generated/product-updates";

export function UpdateEntry({
	entry,
	missingImage = false,
}: {
	entry: ProductUpdateEntry;
	missingImage?: boolean;
}) {
	return (
		<article className="py-6 first:pt-0 last:pb-0">
			<div className="flex flex-col gap-3">
				<div className="min-w-0">
					<p className="text-sm text-muted-foreground">
						{new Intl.DateTimeFormat(undefined, {
							dateStyle: "long",
						}).format(new Date(entry.published_at))}
					</p>
					<h2 className="mt-2 font-display text-xl font-semibold leading-snug [overflow-wrap:anywhere] sm:text-2xl">
						{entry.title}
					</h2>
				</div>
				<div className="flex flex-wrap gap-2">
					<Badge variant="outline">{entry.area}</Badge>
					<Badge variant="secondary">{entry.type}</Badge>
					{entry.action_required && (
						<Badge variant="warning">Action Required</Badge>
					)}
				</div>
			</div>

			<MarkdownContent
				content={entry.markdown}
				className="mt-4 max-w-[75ch]"
				components={{
					a: ({ href, children }) => (
						<a
							href={href}
							target="_blank"
							rel="noopener noreferrer"
							className="text-primary underline underline-offset-2"
						>
							{children}
						</a>
					),
					img: ({ src, alt }) => {
						const asset = entry.assets.find(
							(candidate) =>
								candidate.path === src || candidate.url === src,
						);
						return asset ? (
							<UpdateImage
								asset={asset}
								forceMissing={missingImage}
							/>
						) : (
							<span className="text-muted-foreground">{alt}</span>
						);
					},
				}}
			/>
			{(entry.sources.length > 0 || entry.contributors.length > 0) && (
				<details className="mt-4 text-sm text-muted-foreground">
					<summary className="cursor-pointer">Source Details</summary>
					{entry.sources.length > 0 && (
						<div className="mt-5 flex flex-wrap gap-x-4 gap-y-2 border-t pt-4 text-sm">
							<span className="font-medium">Sources</span>
							{entry.sources.map((source) => {
								const hasPullRequest = source.pr != null;
								const label = hasPullRequest
									? `PR #${source.pr}`
									: source.commit?.slice(0, 7);
								const href = hasPullRequest
									? `https://github.com/gobifrost/bifrost/pull/${source.pr}`
									: source.commit
										? `https://github.com/gobifrost/bifrost/commit/${source.commit}`
										: undefined;
								if (!label || !href) return null;
								return (
									<a
										key={source.pr ?? source.commit}
										className="inline-flex items-center gap-1 text-primary underline underline-offset-2"
										href={href}
										target="_blank"
										rel="noreferrer"
									>
										{label}
										<ExternalLink
											aria-hidden="true"
											className="size-3"
										/>
									</a>
								);
							})}
						</div>
					)}
					{entry.contributors.length > 0 && (
						<p className="mt-3 text-sm text-muted-foreground">
							Credits:{" "}
							{entry.contributors.map((contributor, index) => (
								<span
									key={`${contributor.login}-${contributor.source_pr}-${contributor.role ?? ""}`}
								>
									{index > 0 && ", "}
									<a
										className="text-primary underline underline-offset-2"
										href={contributor.profile_url}
										target="_blank"
										rel="noreferrer"
									>
										{contributor.login}
									</a>{" "}
									(PR #{contributor.source_pr})
								</span>
							))}
						</p>
					)}{" "}
				</details>
			)}
		</article>
	);
}

function UpdateImage({
	asset,
	forceMissing,
}: {
	asset: ProductUpdateAsset;
	forceMissing: boolean;
}) {
	const [failedToLoad, setFailedToLoad] = useState(false);
	const missing = forceMissing || failedToLoad;
	if (missing)
		return (
			<span
				role="alert"
				className="flex min-h-36 items-center gap-3 rounded-[var(--bf-radius-surface)] border border-dashed p-4 text-sm text-muted-foreground"
			>
				<ImageOff aria-hidden="true" /> Screenshot unavailable:{" "}
				{asset.alt}
			</span>
		);
	return (
		<span className="block overflow-hidden rounded-[var(--bf-radius-surface)] border">
			<img
				src={asset.url}
				alt={asset.alt}
				className="h-auto w-full bg-muted"
				loading="lazy"
				onError={() => setFailedToLoad(true)}
			/>
			{asset.caption && (
				<span className="block border-t px-3 py-2 text-sm text-muted-foreground">
					{asset.caption}
				</span>
			)}
		</span>
	);
}

export function CommunityFooter({ className = "" }: { className?: string }) {
	return (
		<footer
			className={`relative shrink-0 border-t bg-background px-5 py-3 ${className}`}
		>
			<div
				aria-hidden="true"
				className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-primary via-fuchsia-500 to-amber-400"
			/>
			<nav
				aria-label="Bifrost community links"
				className="mx-auto flex max-w-4xl flex-wrap items-center justify-center gap-x-5 gap-y-2 text-sm"
			>
				<CommunityLink
					href="https://github.com/gobifrost/bifrost"
					icon={<Github className="size-4" />}
				>
					GitHub
				</CommunityLink>
				<CommunityLink
					href="https://discord.gg/f7TCcWX2s"
					icon={<DiscordIcon className="size-4" />}
				>
					Discord
				</CommunityLink>
				<CommunityLink
					href="https://gobifrost.com"
					icon={<Logo type="square" alt="" className="size-4" />}
				>
					Website
				</CommunityLink>
			</nav>
		</footer>
	);
}
function CommunityLink({
	href,
	icon,
	children,
}: {
	href: string;
	icon: ReactNode;
	children: string;
}) {
	return (
		<a
			className="inline-flex min-h-10 items-center gap-2 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
			href={href}
			target="_blank"
			rel="noreferrer"
		>
			{icon}
			{children}
		</a>
	);
}
