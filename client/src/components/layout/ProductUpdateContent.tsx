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
	ProductUpdateOtherChange,
} from "@/generated/product-updates";

export function UpdateGroups({
	entries,
	missingImage = false,
	changes = [],
}: {
	entries: ProductUpdateEntry[];
	missingImage?: boolean;
	changes?: ProductUpdateOtherChange[];
}) {
	const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "long" });
	const groups = new Map<string, ProductUpdateEntry[]>();
	for (const entry of entries
		.filter((entry) => entry.in_app !== false)
		.sort(
			(a, b) => Date.parse(b.published_at) - Date.parse(a.published_at),
		)) {
		const date = formatter.format(new Date(entry.published_at));
		const group = groups.get(date);
		if (group) group.push(entry);
		else groups.set(date, [entry]);
	}
	return (
		<div className="space-y-8">
			{groups.size === 0 && (
				<CorrectionLists entries={[]} changes={changes} />
			)}
			{Array.from(groups, ([date, updates], index) => (
				<section key={date} aria-label={date}>
					<h2 className="mb-4 text-sm font-medium text-muted-foreground">
						{date}
					</h2>
					{updates.some(
						(entry) =>
							entry.type === "New" || entry.type === "Improved",
					) && (
						<>
							<h3 className="mb-4 text-sm font-semibold">
								New Features and Functionality
							</h3>
							<div className="divide-y divide-border">
								{updates
									.filter(
										(entry) =>
											entry.type === "New" ||
											entry.type === "Improved",
									)
									.map((entry) => (
										<UpdateEntry
											key={entry.id}
											entry={entry}
											missingImage={missingImage}
										/>
									))}
							</div>
						</>
					)}
					<CorrectionLists
						entries={updates}
						changes={index === 0 ? changes : []}
					/>
				</section>
			))}
		</div>
	);
}

export function UpdateEntry({
	entry,
	missingImage = false,
	compact = false,
}: {
	entry: ProductUpdateEntry;
	missingImage?: boolean;
	compact?: boolean;
}) {
	return (
		<article className={compact ? "text-sm" : "py-6 first:pt-0 last:pb-0"}>
			{!compact && (
				<div className="flex flex-col gap-3">
					<div className="min-w-0">
						<h4
							className={
								compact
									? "font-semibold"
									: "font-display text-xl font-semibold leading-snug [overflow-wrap:anywhere] sm:text-2xl"
							}
						>
							{entry.title}
						</h4>
					</div>
					{entry.action_required && (
						<div className="flex flex-wrap gap-2">
							<Badge variant="warning">Action Required</Badge>
						</div>
					)}
				</div>
			)}
			{compact && entry.action_required && (
				<Badge variant="warning" className="mb-1">
					Action Required
				</Badge>
			)}

			<MarkdownContent
				content={entry.markdown}
				className={
					compact ? "mt-1 max-w-[75ch] text-sm" : "mt-4 max-w-[75ch]"
				}
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
			{!compact &&
				(entry.sources.length > 0 || entry.contributors.length > 0) && (
					<details className="mt-4 text-sm text-muted-foreground">
						<summary className="cursor-pointer">
							Source Details
						</summary>
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
								{entry.contributors.map(
									(contributor, index) => (
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
									),
								)}
							</p>
						)}{" "}
					</details>
				)}
		</article>
	);
}

function CorrectionLists({
	entries,
	changes,
}: {
	entries: ProductUpdateEntry[];
	changes: ProductUpdateOtherChange[];
}) {
	return (
		<>
			{(["Fixed", "Security"] as const).map((type) => {
				const notes = entries.filter((entry) => entry.type === type);
				const fixes = changes.filter(
					(change) =>
						(change.category ?? "fix") ===
						(type === "Fixed" ? "fix" : "hardening"),
				);
				return (
					(notes.length > 0 || fixes.length > 0) && (
						<section key={type} className="mt-6">
							<h3 className="mb-3 text-sm font-semibold">
								{type === "Fixed" ? "Bug Fixes" : "Hardening"}
							</h3>
							<ul className="list-disc space-y-3 pl-5 text-sm">
								{notes.map((entry) => (
									<li key={entry.id}>
										<UpdateEntry entry={entry} compact />
									</li>
								))}
								{fixes.map((change) => (
									<li key={change.source}>
										<a
											href={change.url}
											target="_blank"
											rel="noopener noreferrer"
											className="underline decoration-muted-foreground/40 underline-offset-2"
										>
											{change.title}
										</a>
										{change.contributors.map((person) => (
											<span
												key={`${person.login}-${person.source_pr}`}
												className="ml-1 text-muted-foreground"
											>
												—{" "}
												<a
													href={person.profile_url}
													target="_blank"
													rel="noopener noreferrer"
												>
													{person.login}
												</a>
											</span>
										))}
									</li>
								))}
							</ul>
							{notes.some(
								(entry) =>
									entry.sources.length > 0 ||
									entry.contributors.length > 0,
							) && (
								<details className="mt-3 text-xs text-muted-foreground">
									<summary className="cursor-pointer">
										Source Details
									</summary>
									<div className="mt-2 space-y-2">
										{notes.map((entry) => (
											<div key={entry.id}>
												<span>{entry.title}: </span>
												{entry.sources.map((source) => (
													<a
														key={
															source.pr ??
															source.commit
														}
														className="mr-2 underline"
														href={
															source.pr
																? `https://github.com/gobifrost/bifrost/pull/${source.pr}`
																: `https://github.com/gobifrost/bifrost/commit/${source.commit}`
														}
														target="_blank"
														rel="noopener noreferrer"
													>
														{source.pr
															? `PR #${source.pr}`
															: source.commit?.slice(
																	0,
																	7,
																)}
													</a>
												))}
												{entry.contributors.map(
													(person) => (
														<a
															key={`${person.login}-${person.source_pr}`}
															href={
																person.profile_url
															}
															className="mr-2 underline"
															target="_blank"
															rel="noopener noreferrer"
														>
															{person.login}
														</a>
													),
												)}
											</div>
										))}
									</div>
								</details>
							)}
						</section>
					)
				);
			})}
		</>
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

export function CommunityFooter({
	className = "",
	children,
}: {
	className?: string;
	children?: ReactNode;
}) {
	return (
		<footer
			className={`relative shrink-0 border-t bg-background px-3 py-1 ${className}`}
		>
			<div
				aria-hidden="true"
				className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-primary via-fuchsia-500 to-amber-400"
			/>
			<div
				className={`mx-auto flex max-w-4xl items-center gap-1 ${children ? "justify-between" : "justify-center"}`}
			>
				<nav
					aria-label="Bifrost community links"
					className="flex items-center justify-center gap-1 text-sm sm:gap-4"
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
				{children}
			</div>
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
			aria-label={children}
			title={children}
			className="inline-flex min-h-10 min-w-10 items-center justify-center gap-2 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
			href={href}
			target="_blank"
			rel="noreferrer"
		>
			{icon}
			<span className="hidden sm:inline">{children}</span>
		</a>
	);
}
