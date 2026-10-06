import { describe, expect, it, vi } from "vitest";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProductUpdatesMenuItem } from "./ProductUpdatesMenuItem";

vi.mock("@/lib/product-updates-preview", () => ({
	PRODUCT_UPDATES_RECEIPTS_EVENT: "bifrost:product-updates-receipts",
	productUpdatesPreviewAdapter: {
		getBundle: vi.fn(async () => ({ entries: [{ id: "entry-1" }] })),
		getReadEntryIds: vi.fn(async () => new Set()),
	},
}));

describe("ProductUpdatesMenuItem", () => {
	it("opens the update feed and exposes a quiet unread count", async () => {
		const onOpen = vi.fn();
		const user = userEvent.setup();
		render(
			<DropdownMenu>
				<DropdownMenuTrigger>Account</DropdownMenuTrigger>
				<DropdownMenuContent>
					<ProductUpdatesMenuItem adminId="admin-a" onOpen={onOpen} />
				</DropdownMenuContent>
			</DropdownMenu>,
		);
		await user.click(screen.getByRole("button", { name: "Account" }));
		expect(
			await screen.findByLabelText("1 unread product updates"),
		).toBeVisible();
		await user.click(
			screen.getByRole("menuitem", { name: /^What's New/ }),
		);
		expect(onOpen).toHaveBeenCalledOnce();
	});
});
