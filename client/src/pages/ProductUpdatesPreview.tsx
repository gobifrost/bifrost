import { useEffect, useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { AlertCircle, LoaderCircle, Megaphone, X } from "lucide-react";
import {
	CommunityFooter,
	UpdateGroups,
} from "@/components/layout/ProductUpdateContent";
import { useAuth } from "@/contexts/AuthContext";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
	productUpdatesPreviewAdapter,
	visibleProductUpdates,
	withFuturePreviewCandidate,
	type ProductUpdatesAdapter,
	type ProductUpdatesPreviewState,
} from "@/lib/product-updates-preview";
import type {
	ProductUpdateEntry,
	ProductUpdateOtherChange,
} from "@/generated/product-updates";

const previewStates: { value: ProductUpdatesPreviewState; label: string }[] = [
	{ value: "normal", label: "Normal" },
	{ value: "empty", label: "Empty" },
	{ value: "loading", label: "Loading" },
	{ value: "failure", label: "Failure" },
	{ value: "missingimage", label: "Missing Image" },
	{ value: "rollback", label: "Rollback" },
	{ value: "future", label: "Future Entry" },
];

export function ProductUpdatesPreview({
	adapter = productUpdatesPreviewAdapter,
}: {
	adapter?: ProductUpdatesAdapter;
}) {
	const [searchParams, setSearchParams] = useSearchParams();
	const { user } = useAuth();
	const showControls = searchParams.get("controls") === "1";
	const state = readPreviewState(searchParams.get("state"));
	const selectedAdmin = searchParams.get("admin") ?? "current";
	const adminId =
		selectedAdmin === "admin-b"
			? "admin-b"
			: selectedAdmin === "admin-a"
				? "admin-a"
				: (user?.id ?? "preview-admin");
	const channel = searchParams.get("channel") === "stable" ? "stable" : "dev";
	const [entries, setEntries] = useState<ProductUpdateEntry[] | null>(null);
	const [otherChanges, setOtherChanges] = useState<
		ProductUpdateOtherChange[]
	>([]);
	const [failedRequestKey, setFailedRequestKey] = useState<string | null>(
		null,
	);
	const [loadedRequestKey, setLoadedRequestKey] = useState<string | null>(
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
		void adapter
			.getBundle(channel)
			.then((bundle) => {
				if (!active) return;
				setEntries(
					state === "future"
						? withFuturePreviewCandidate(bundle.entries)
						: bundle.entries,
				);
				setOtherChanges(bundle.other_changes);
				setFailedRequestKey(null);
				setLoadedRequestKey(requestKey);
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

	const change = (key: string, value: string) => {
		const next = new URLSearchParams(searchParams);
		if (value) next.set(key, value);
		else next.delete(key);
		setSearchParams(next, { replace: true });
	};
	useEffect(() => {
		if (
			loadedRequestKey !== requestKey ||
			!entries ||
			state === "loading" ||
			state === "failure" ||
			failedRequestKey === requestKey
		)
			return;
		let active = true;
		void adapter
			.markRead(
				adminId,
				visibleEntries.map((entry) => entry.id),
			)
			.then(() => {
				if (active) setReceiptError(false);
			})
			.catch(() => {
				if (active) setReceiptError(true);
			});
		return () => {
			active = false;
		};
	}, [
		adapter,
		adminId,
		entries,
		visibleEntries,
		state,
		failedRequestKey,
		requestKey,
		loadedRequestKey,
	]);

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col gap-6">
				<ListPageHeader
					title="What's New"
					titleAccessory={
						<>
							<Badge
								variant="warning"
								className="hidden sm:inline-flex"
							>
								Preview · Draft Backfill
							</Badge>
							<Button
								asChild
								variant="ghost"
								className="ml-auto h-10 px-3"
							>
								<Link to="/">
									<X aria-hidden="true" className="size-4" />
									Done
								</Link>
							</Button>
						</>
					}
					description="New features, improvements, and fixes in Bifrost."
				/>

				{showControls && (
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
												change(
													"state",
													event.target.value,
												)
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
												change(
													"admin",
													event.target.value,
												)
											}
										>
											<option value="current">
												Current Admin
											</option>
											<option value="admin-b">
												Admin B
											</option>
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
											<option value="stable">
												Stable
											</option>
										</select>
									</label>
									<span>
										Receipts use stable entry UUIDs across
										Dev and Stable.
									</span>
								</AlertDescription>
							</Alert>
						</div>
					</details>
				)}

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
						<AlertTitle>Seen State Wasn't Saved</AlertTitle>
						<AlertDescription>
							Your updates are still available. This preview could
							not save which updates were shown in this browser.
						</AlertDescription>
					</Alert>
				)}
				<div className="min-h-0 overflow-auto pr-1 pb-3">
					{state === "failure" || failedRequestKey === requestKey ? (
						<FeedFailure />
					) : state === "loading" ||
					  entries === null ||
					  loadedRequestKey !== requestKey ? (
						<FeedLoading />
					) : (
						<>
							{visibleEntries.length === 0 &&
							otherChanges.length === 0 ? (
								<EmptyState
									icon={Megaphone}
									title="No Updates Yet"
									description="New product updates will appear here."
								/>
							) : (
								<UpdateGroups
									entries={visibleEntries}
									missingImage={state === "missingimage"}
									changes={otherChanges}
								/>
							)}
						</>
					)}
				</div>
			</div>
			<CommunityFooter className="-mx-4 sm:-mx-6 lg:-mx-8" />
		</div>
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
