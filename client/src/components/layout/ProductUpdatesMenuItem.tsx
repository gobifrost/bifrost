import { useEffect, useState } from "react";
import { Megaphone } from "lucide-react";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import {
	PRODUCT_UPDATES_RECEIPTS_EVENT,
	productUpdatesPreviewAdapter,
} from "@/lib/product-updates-preview";

/** Development-only account-menu entry with a deliberately quiet unread cue. */
export function ProductUpdatesMenuItem({
	adminId,
	onOpen,
}: {
	adminId: string;
	onOpen: () => void;
}) {
	const [unreadCount, setUnreadCount] = useState(0);

	useEffect(() => {
		let active = true;
		const refresh = () => {
			void Promise.all([
				productUpdatesPreviewAdapter.getBundle("dev"),
				productUpdatesPreviewAdapter.getReadEntryIds(adminId),
			])
				.then(([bundle, receipts]) => {
					if (!active) return;
					setUnreadCount(
						bundle.entries.filter(
							(entry) => !receipts.has(entry.id),
						).length,
					);
				})
				.catch(() => {
					if (active) setUnreadCount(0);
				});
		};
		refresh();
		window.addEventListener(PRODUCT_UPDATES_RECEIPTS_EVENT, refresh);
		window.addEventListener("storage", refresh);
		return () => {
			active = false;
			window.removeEventListener(PRODUCT_UPDATES_RECEIPTS_EVENT, refresh);
			window.removeEventListener("storage", refresh);
		};
	}, [adminId]);

	return (
		<DropdownMenuItem className="min-h-11" onSelect={onOpen}>
			<Megaphone aria-hidden="true" className="size-4" />
			<span className="flex min-w-0 flex-1 items-center justify-between gap-2">
				What's New
				{unreadCount > 0 && (
					<span
						aria-label={`${unreadCount} unread product updates`}
						className="size-2 rounded-full bg-primary"
					/>
				)}
			</span>
		</DropdownMenuItem>
	);
}
