import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogDescription,
} from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { CommunityFooter, UpdateGroups } from "./ProductUpdateContent";
import {
	productUpdatesAdapter,
	visibleProductUpdates,
	type ProductUpdatesAdapter,
	type ProductUpdateEntry,
} from "@/services/productUpdates";

const PRODUCT_UPDATES_CHANNEL = "bifrost:product-updates-presented";

type PresentedMessage = { adminId: string; entryIds: string[] };

function isPresentedMessage(value: unknown): value is PresentedMessage {
	return (
		typeof value === "object" &&
		value !== null &&
		"adminId" in value &&
		typeof value.adminId === "string" &&
		"entryIds" in value &&
		Array.isArray(value.entryIds) &&
		value.entryIds.every((entryId) => typeof entryId === "string")
	);
}

/** Presents server-authorized announcements once for the signed-in admin. */
export function ProductUpdatesDialog({
	adminId,
	adapter = productUpdatesAdapter,
}: {
	adminId: string;
	adapter?: ProductUpdatesAdapter;
}) {
	const { pathname } = useLocation();
	const initialPath = useRef(pathname);
	const title = useRef<HTMLHeadingElement>(null);
	const channel = useRef<BroadcastChannel | null>(null);
	const acknowledgedBatches = useRef(new Set<string>());
	const presentedElsewhereRef = useRef(new Set<string>());
	const [presentedElsewhere, setPresentedElsewhere] = useState<
		ReadonlySet<string>
	>(new Set());
	const [entries, setEntries] = useState<ProductUpdateEntry[]>([]);
	const [open, setOpen] = useState(false);
	const [receiptError, setReceiptError] = useState(false);

	useEffect(() => {
		if (typeof BroadcastChannel === "undefined") return;
		const nextChannel = new BroadcastChannel(PRODUCT_UPDATES_CHANNEL);
		channel.current = nextChannel;
		nextChannel.onmessage = (event: MessageEvent<unknown>) => {
			if (
				!isPresentedMessage(event.data) ||
				event.data.adminId !== adminId
			)
				return;
			const presented = event.data;
			setPresentedElsewhere((current) => {
				const next = new Set(current);
				for (const entryId of presented.entryIds) {
					presentedElsewhereRef.current.add(entryId);
					next.add(entryId);
				}
				return next;
			});
		};
		return () => {
			nextChannel.close();
			if (channel.current === nextChannel) channel.current = null;
		};
	}, [adminId]);

	useEffect(() => {
		let active = true;
		// History itself presents the updates; avoid covering it on direct visits.
		if (initialPath.current === "/whats-new") return;
		void adapter
			.getFeed()
			.then((feed) => {
				if (!active || document.querySelector('[role="dialog"]'))
					return;
				const unseen = visibleProductUpdates(
					feed.bundle.entries,
				).filter(
					(entry) =>
						!feed.seen_entry_ids.includes(entry.id) &&
						!presentedElsewhereRef.current.has(entry.id),
				);
				if (unseen.length === 0) return;
				setEntries(unseen);
				setOpen(true);
			})
			.catch(() => {
				// Automatic announcements stay quiet if the authenticated feed fails.
			});
		return () => {
			active = false;
		};
	}, [adapter, presentedElsewhere]);

	useEffect(() => {
		if (!open || entries.length === 0) return;
		const entryIds = entries.map((entry) => entry.id);
		const batchKey = [...entryIds].sort().join(",");
		if (acknowledgedBatches.current.has(batchKey)) return;
		acknowledgedBatches.current.add(batchKey);
		channel.current?.postMessage({
			adminId,
			entryIds,
		} satisfies PresentedMessage);

		let active = true;
		void adapter.acknowledge(entryIds).catch(() => {
			if (active) setReceiptError(true);
		});
		return () => {
			active = false;
		};
	}, [adapter, adminId, entries, open]);

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogContent
				className="top-4 flex max-h-[calc(100dvh-2rem)] max-w-2xl flex-col gap-0 overflow-hidden p-0 translate-y-0"
				onOpenAutoFocus={(event) => {
					event.preventDefault();
					title.current?.focus();
				}}
			>
				<DialogHeader className="shrink-0 border-b px-6 py-5 sm:px-8">
					<DialogTitle
						ref={title}
						tabIndex={-1}
						className="font-display text-2xl font-semibold outline-none"
					>
						What's New
					</DialogTitle>
					<DialogDescription>
						The latest improvements to Bifrost.
					</DialogDescription>
				</DialogHeader>
				<div className="min-h-0 overflow-auto px-6 py-6 sm:px-8">
					{receiptError && (
						<Alert className="mb-5">
							<AlertDescription>
								These updates couldn't be saved as seen. They'll
								still be available in What's New.
							</AlertDescription>
						</Alert>
					)}
					<UpdateGroups entries={entries} />
				</div>
				<CommunityFooter>
					<div className="flex items-center gap-1">
						<Button
							asChild
							variant="ghost"
							className="h-10 px-2 text-xs sm:text-sm"
						>
							<a
								href="/whats-new"
								target="_blank"
								rel="noopener noreferrer"
							>
								View All Updates{" "}
								<ArrowRight
									aria-hidden="true"
									className="size-4"
								/>
							</a>
						</Button>
						<Button
							variant="outline"
							className="h-10 px-3"
							onClick={() => setOpen(false)}
						>
							Done
						</Button>
					</div>
				</CommunityFooter>
			</DialogContent>
		</Dialog>
	);
}
