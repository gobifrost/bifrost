import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, LoaderCircle, Megaphone, X } from "lucide-react";
import {
	CommunityFooter,
	UpdateGroups,
} from "@/components/layout/ProductUpdateContent";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
	productUpdatesAdapter,
	visibleProductUpdates,
	type ProductUpdatesAdapter,
	type ProductUpdateEntry,
	type ProductUpdateOtherChange,
} from "@/services/productUpdates";

export function ProductUpdates({
	adapter = productUpdatesAdapter,
}: {
	adapter?: ProductUpdatesAdapter;
}) {
	const acknowledgedBatches = useRef(new Set<string>());
	const [entries, setEntries] = useState<ProductUpdateEntry[] | null>(null);
	const [otherChanges, setOtherChanges] = useState<
		ProductUpdateOtherChange[]
	>([]);
	const [failed, setFailed] = useState(false);
	const [receiptError, setReceiptError] = useState(false);

	useEffect(() => {
		let active = true;
		void adapter
			.getFeed()
			.then((feed) => {
				if (!active) return;
				setEntries(visibleProductUpdates(feed.bundle.entries));
				setOtherChanges(feed.bundle.other_changes);
				setFailed(false);
			})
			.catch(() => {
				if (active) setFailed(true);
			});
		return () => {
			active = false;
		};
	}, [adapter]);

	useEffect(() => {
		if (!entries || entries.length === 0) return;
		const entryIds = entries.map((entry) => entry.id);
		const batchKey = [...entryIds].sort().join(",");
		if (acknowledgedBatches.current.has(batchKey)) return;
		acknowledgedBatches.current.add(batchKey);

		let active = true;
		void adapter.acknowledge(entryIds).catch(() => {
			if (active) setReceiptError(true);
		});
		return () => {
			active = false;
		};
	}, [adapter, entries]);

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col gap-6">
				<ListPageHeader
					title="What's New"
					titleAccessory={
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
					}
					description="New features, improvements, and fixes in Bifrost."
				/>

				{receiptError && (
					<Alert>
						<AlertCircle aria-hidden="true" />
						<AlertTitle>Seen State Wasn't Saved</AlertTitle>
						<AlertDescription>
							Your updates are still available. We couldn't save
							that they were shown.
						</AlertDescription>
					</Alert>
				)}
				<div className="min-h-0 overflow-auto pr-1 pb-3">
					{failed ? (
						<FeedFailure />
					) : entries === null ? (
						<FeedLoading />
					) : entries.length === 0 && otherChanges.length === 0 ? (
						<EmptyState
							icon={Megaphone}
							title="No Updates Yet"
							description="New product updates will appear here."
						/>
					) : (
						<UpdateGroups
							entries={entries}
							changes={otherChanges}
						/>
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
				Check your connection and try again. No updates were marked as
				seen.
			</AlertDescription>
		</Alert>
	);
}
