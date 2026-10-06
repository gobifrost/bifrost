import { useEffect, useState } from "react";
import { Megaphone } from "lucide-react";
import {
	PRODUCT_UPDATES_RECEIPTS_EVENT,
	productUpdatesPreviewAdapter,
} from "@/lib/product-updates-preview";
import { SidebarLink } from "./sidebarLinks";

/** Development-only navigation item whose unread cue shares the receipt adapter. */
export function ProductUpdatesSidebarLink({
	adminId,
	isCollapsed,
	onClick,
}: {
	adminId: string;
	isCollapsed: boolean;
	onClick?: () => void;
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
		<SidebarLink
			to="/whats-new"
			label="What's New"
			icon={Megaphone}
			isCollapsed={isCollapsed}
			onClick={onClick}
		>
			<span className="flex min-w-0 flex-1 items-center justify-between gap-2">
				<span className="min-w-0 break-words">What's New</span>
				{unreadCount > 0 && (
					<span
						aria-label={`${unreadCount} unread product updates`}
						className="size-2 shrink-0 rounded-full bg-primary"
					/>
				)}
			</span>
		</SidebarLink>
	);
}
