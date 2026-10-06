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
	productUpdatesPreviewAdapter,
	type ProductUpdatesAdapter,
} from "@/lib/product-updates-preview";
import type { ProductUpdateEntry } from "@/generated/product-updates";

/** Development-only announcement; opening the displayed batch acknowledges it. */
export function ProductUpdatesDialog({
	adminId,
	adapter = productUpdatesPreviewAdapter,
}: {
	adminId: string;
	adapter?: ProductUpdatesAdapter;
}) {
	const { pathname } = useLocation();
	const initialPath = useRef(pathname);
	const title = useRef<HTMLHeadingElement>(null);
	const [entries, setEntries] = useState<ProductUpdateEntry[]>([]);
	const [open, setOpen] = useState(false);
	const [receiptError, setReceiptError] = useState(false);

	useEffect(() => {
		let active = true;
		// History itself presents the updates; avoid covering it on direct visits.
		if (initialPath.current === "/whats-new") return;
		void Promise.all([
			adapter.getBundle("dev"),
			adapter.getReadEntryIds(adminId),
		])
			.then(([bundle, receipts]) => {
				if (!active || document.querySelector('[role="dialog"]'))
					return;
				const unseen = bundle.entries.filter(
					(entry) => !receipts.has(entry.id),
				);
				if (unseen.length === 0) return;
				setEntries(unseen);
				setOpen(true);
			})
			.catch(() => {
				// Automatic announcements are optional; history stays available.
				// No receipt is saved when loading fails.
			});
		return () => {
			active = false;
		};
	}, [adminId, adapter]);

	useEffect(() => {
		if (!open || entries.length === 0) return;
		let active = true;
		void adapter
			.markRead(
				adminId,
				entries.map((entry) => entry.id),
			)
			.catch(() => {
				if (active) setReceiptError(true);
			});
		return () => {
			active = false;
		};
	}, [open, entries, adapter, adminId]);

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
				<div className="flex shrink-0 justify-end border-t px-6 py-4 sm:px-8">
					<Button
						asChild
						variant="outline"
						className="h-11 gap-2 px-4"
					>
						<a
							href="/whats-new"
							target="_blank"
							rel="noopener noreferrer"
						>
							View All Updates{" "}
							<ArrowRight aria-hidden="true" className="size-4" />
						</a>
					</Button>
				</div>
				<CommunityFooter />
			</DialogContent>
		</Dialog>
	);
}
