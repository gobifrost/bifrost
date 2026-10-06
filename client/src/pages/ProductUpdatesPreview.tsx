import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
	AlertCircle,
	Check,
	ExternalLink,
	ImageOff,
	LoaderCircle,
	Megaphone,
} from "lucide-react";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import { Logo } from "@/components/branding/Logo";
import { DiscordIcon } from "@/components/icons/DiscordIcon";
import { useAuth } from "@/contexts/AuthContext";
import { Github } from "@/components/icons/GithubIcon";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	productUpdatesPreviewAdapter,
	visibleProductUpdates,
	withFuturePreviewCandidate,
	type ProductUpdatesAdapter,
	type ProductUpdatesPreviewState,
} from "@/lib/product-updates-preview";
import type {
	ProductUpdateAsset,
	ProductUpdateEntry,
	ProductUpdateOtherChange,
} from "@/generated/product-updates";

const previewStates: { value: ProductUpdatesPreviewState; label: string }[] = [
	{ value: "normal", label: "Normal" },
	{ value: "unread", label: "Unread" },
	{ value: "allread", label: "All Read" },
	{ value: "empty", label: "Empty" },
	{ value: "loading", label: "Loading" },
	{ value: "failure", label: "Failure" },
	{ value: "missingimage", label: "Missing Image" },
	{ value: "rollback", label: "Rollback" },
	{ value: "future", label: "Future Entry" },
];

type FeedView = "unread" | "all";

export function ProductUpdatesPreview({
	adapter = productUpdatesPreviewAdapter,
}: {
	adapter?: ProductUpdatesAdapter;
}) {
	const [searchParams, setSearchParams] = useSearchParams();
	const { user } = useAuth();
	const state = readPreviewState(searchParams.get("state"));
	const selectedAdmin = searchParams.get("admin") ?? "current";
	const adminId =
		selectedAdmin === "admin-b"
			? "admin-b"
			: selectedAdmin === "admin-a"
				? "admin-a"
				: (user?.id ?? "preview-admin");
	const channel = searchParams.get("channel") === "stable" ? "stable" : "dev";
	const view: FeedView =
		searchParams.get("view") === "unread" ? "unread" : "all";
	const [entries, setEntries] = useState<ProductUpdateEntry[] | null>(null);
	const [otherChanges, setOtherChanges] = useState<
		ProductUpdateOtherChange[]
	>([]);
	const [readIds, setReadIds] = useState<ReadonlySet<string>>(new Set());
	const [failedRequestKey, setFailedRequestKey] = useState<string | null>(
		null,
	);
	const [receiptError, setReceiptError] = useState(false);
	const requestKey = `${adminId}:${channel}:${state}`;

	useEffect(() => {
		let active = true;
		if (state === "loading" || state === "failure") {
			return () => {
				active = false;
			};
		}
		void Promise.all([
			adapter.getBundle(channel),
			adapter.getReadEntryIds(adminId),
		])
			.then(([bundle, receipts]) => {
				if (!active) return;
				setEntries(
					state === "future"
						? withFuturePreviewCandidate(bundle.entries)
						: bundle.entries,
				);
				setOtherChanges(bundle.other_changes);
				setFailedRequestKey(null);
				setReadIds(
					state === "unread"
						? new Set()
						: state === "allread"
							? new Set(bundle.entries.map((entry) => entry.id))
							: receipts,
				);
			})
			.catch(() => {
				if (active) setFailedRequestKey(requestKey);
			});
		return () => {
			active = false;
		};
	}, [adapter, adminId, channel, requestKey, state]);

	const visibleEntries = useMemo(
		() => (entries ? visibleProductUpdates(entries, state) : []),
		[entries, state],
	);
	const presentedEntries =
		view === "unread"
			? visibleEntries.filter((entry) => !readIds.has(entry.id))
			: visibleEntries;

	const change = (key: string, value: string) => {
		const next = new URLSearchParams(searchParams);
		if (value) next.set(key, value);
		else next.delete(key);
		setSearchParams(next, { replace: true });
	};
	const markPresentedRead = async () => {
		const entryIds = presentedEntries.map((entry) => entry.id);
		try {
			await adapter.markRead(adminId, entryIds);
			setReadIds((previous) => new Set([...previous, ...entryIds]));
			setReceiptError(false);
		} catch {
			setReceiptError(true);
		}
	};

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col gap-6">
				<ListPageHeader
					title="What's New"
					titleAccessory={
						<Badge variant="warning">
							Preview · Draft Backfill
						</Badge>
					}
					description="A development-only preview of product updates. Draft copy and read receipts are local to this browser."
				/>

				<details className="rounded-[var(--bf-radius-surface)] border bg-muted/20 px-4 py-3">
					<summary className="cursor-pointer font-medium">
						Preview Controls
					</summary>
					<div className="mt-3">
						<Alert>
							<Megaphone aria-hidden="true" />
							<AlertTitle>Development Fixture</AlertTitle>
							<AlertDescription className="flex flex-wrap items-center gap-3">
								<label className="flex items-center gap-2">
									<span>State</span>
									<select
										aria-label="Preview state"
										className="h-9 rounded-[var(--bf-radius-control)] border bg-background px-2"
										value={state}
										onChange={(event) =>
											change("state", event.target.value)
										}
									>
										{previewStates.map((option) => (
											<option
												key={option.value}
												value={option.value}
											>
												{option.label}
											</option>
										))}
									</select>
								</label>
								<label className="flex items-center gap-2">
									<span>Admin</span>
									<select
										aria-label="Preview admin"
										className="h-9 rounded-[var(--bf-radius-control)] border bg-background px-2"
										value={selectedAdmin}
										onChange={(event) =>
											change("admin", event.target.value)
										}
									>
										<option value="current">
											Current Admin
										</option>
										<option value="admin-b">Admin B</option>
									</select>
								</label>
								<label className="flex items-center gap-2">
									<span>Bundle</span>
									<select
										aria-label="Preview bundle"
										className="h-9 rounded-[var(--bf-radius-control)] border bg-background px-2"
										value={channel}
										onChange={(event) =>
											change(
												"channel",
												event.target.value,
											)
										}
									>
										<option value="dev">Dev</option>
										<option value="stable">Stable</option>
									</select>
								</label>
								<span>
									Receipts use stable entry UUIDs across Dev
									and Stable.
								</span>
							</AlertDescription>
						</Alert>
					</div>
				</details>

				{state === "future" && (
					<Alert>
						<AlertCircle aria-hidden="true" />
						<AlertTitle>Future Entry Withheld</AlertTitle>
						<AlertDescription>
							The preview fixture withholds a future-entry
							candidate. Runtime availability always comes from
							the generated bundle.
						</AlertDescription>
					</Alert>
				)}
				{state === "rollback" && (
					<Alert>
						<AlertCircle aria-hidden="true" />
						<AlertTitle>Older Bundle Preview</AlertTitle>
						<AlertDescription>
							The newest eligible entry is withheld to model a
							bundle rollback.
						</AlertDescription>
					</Alert>
				)}
				{receiptError && (
					<Alert variant="destructive">
						<AlertCircle aria-hidden="true" />
						<AlertTitle>Read State Wasn't Saved</AlertTitle>
						<AlertDescription>
							Try marking these updates read again. This preview
							keeps receipts only in this browser.
						</AlertDescription>
					</Alert>
				)}
				<div className="max-h-[min(42rem,calc(100dvh-23rem))] overflow-auto pr-1">
					{state === "failure" || failedRequestKey === requestKey ? (
						<FeedFailure />
					) : state === "loading" || entries === null ? (
						<FeedLoading />
					) : (
						<>
							<div className="flex flex-wrap items-center justify-between gap-3">
								<Tabs
									value={view}
									onValueChange={(value) =>
										change("view", value)
									}
								>
									<TabsList aria-label="Update history view">
										<TabsTrigger value="all">
											All History
										</TabsTrigger>
										<TabsTrigger value="unread">
											Unread (
											{
												visibleEntries.filter(
													(entry) =>
														!readIds.has(entry.id),
												).length
											}
											)
										</TabsTrigger>
									</TabsList>
								</Tabs>
								<Button
									type="button"
									variant="outline"
									onClick={() => void markPresentedRead()}
									disabled={presentedEntries.length === 0}
								>
									<Check aria-hidden="true" /> Mark Presented
									Updates Read
								</Button>
							</div>
							{presentedEntries.length === 0 ? (
								<EmptyState
									icon={Megaphone}
									title={
										view === "unread"
											? "You're Caught Up"
											: "No Updates Yet"
									}
									description={
										view === "unread"
											? "There are no unread product updates in this bundle."
											: "No eligible product updates are available in this preview bundle."
									}
								/>
							) : (
								<div className="space-y-4">
									{presentedEntries.map((entry) => (
										<UpdateEntry
											key={entry.id}
											entry={entry}
											unread={!readIds.has(entry.id)}
											missingImage={
												state === "missingimage"
											}
										/>
									))}
								</div>
							)}
							{view === "all" && otherChanges.length > 0 && (
								<section
									aria-labelledby="other-changes-heading"
									className="mt-8"
								>
									<h2
										id="other-changes-heading"
										className="font-display text-lg font-semibold"
									>
										Other Changes
									</h2>
									<ul className="mt-3 space-y-2 text-sm">
										{otherChanges.map((change) => (
											<li key={change.source}>
												<a
													className="text-primary underline underline-offset-2"
													href={change.url}
													target="_blank"
													rel="noreferrer"
												>
													{change.title}
												</a>
											</li>
										))}
									</ul>
								</section>
							)}
						</>
					)}
				</div>
			</div>
			<CommunityFooter />
		</div>
	);
}

function UpdateEntry({
	entry,
	unread,
	missingImage,
}: {
	entry: ProductUpdateEntry;
	unread: boolean;
	missingImage: boolean;
}) {
	return (
		<article className="rounded-[var(--bf-radius-surface)] border bg-card p-4 shadow-sm sm:p-6">
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div className="min-w-0">
					<p className="text-sm text-muted-foreground">
						{new Intl.DateTimeFormat(undefined, {
							dateStyle: "long",
						}).format(new Date(entry.published_at))}
					</p>
					<h2 className="mt-1 font-display text-xl font-semibold [overflow-wrap:anywhere]">
						{entry.title}
					</h2>
				</div>
				<div className="flex flex-wrap gap-2">
					{unread && <Badge>Unread</Badge>}
					<Badge variant="outline">{entry.area}</Badge>
					<Badge variant="secondary">{entry.type}</Badge>
					{entry.action_required && (
						<Badge variant="warning">Action Required</Badge>
					)}
				</div>
			</div>
			{entry.action_required && (
				<Alert className="mt-4 border-[var(--bf-warning)]/40">
					<AlertCircle aria-hidden="true" />
					<AlertTitle>Action Required</AlertTitle>
					<AlertDescription>
						Review this update before continuing with affected work.
					</AlertDescription>
				</Alert>
			)}
			<MarkdownContent
				content={entry.markdown}
				className="mt-4 max-w-[75ch]"
				components={{
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

function CommunityFooter() {
	return (
		<footer className="relative -mx-4 border-t bg-background/95 px-4 pt-3 pb-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
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
function FeedLoading() {
	return (
		<div
			aria-busy="true"
			aria-label="Loading product updates"
			className="space-y-4"
		>
			<div className="flex items-center gap-2 text-sm text-muted-foreground">
				<LoaderCircle
					aria-hidden="true"
					className="size-4 animate-spin motion-reduce:animate-none"
				/>{" "}
				Loading product updates
			</div>
			<Skeleton className="h-56 w-full" />
			<Skeleton className="h-40 w-full" />
		</div>
	);
}
function FeedFailure() {
	return (
		<Alert variant="destructive">
			<AlertCircle aria-hidden="true" />
			<AlertTitle>Couldn't Load Product Updates</AlertTitle>
			<AlertDescription>
				This preview intentionally simulates a failed bundle request.
				Try another preview state to continue reviewing the feed.
			</AlertDescription>
		</Alert>
	);
}
function readPreviewState(value: string | null): ProductUpdatesPreviewState {
	return previewStates.some((state) => state.value === value)
		? (value as ProductUpdatesPreviewState)
		: "normal";
}
