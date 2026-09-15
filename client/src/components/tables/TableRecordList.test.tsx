import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import type { TablePublic } from "@/services/tables";
import { TableRecordList } from "./TableRecordList";

const table = {
	id: "customers",
	name: "Customers",
	description: "Customer records",
	organization_id: null,
	created_at: null,
} as TablePublic;
function renderList(selectionMode = false, busy = false) {
	const onOpen = vi.fn();
	const onToggle = vi.fn();
	const result = renderWithProviders(
		<TableRecordList
			tables={[table]}
			isPlatformAdmin
			selectionMode={selectionMode}
			selectedIds={new Set()}
			allVisibleSelected={false}
			busy={busy}
			onToggleAll={vi.fn()}
			onToggle={onToggle}
			onOpen={onOpen}
			scopeName={() => "Global"}
			formatDate={() => "Today"}
			renderActions={() => null}
		/>,
	);
	return { ...result, onOpen, onToggle };
}
describe("TableRecordList navigation", () => {
	it("leaves modified clicks to the native link", () => {
		const { onOpen } = renderList();
		const link = screen.getByRole("link", { name: "Customers" });
		expect(link).toHaveAttribute("href", "/tables/customers");
		expect(fireEvent.click(link, { ctrlKey: true })).toBe(true);
		expect(onOpen).not.toHaveBeenCalled();
		fireEvent.click(link);
		expect(onOpen).toHaveBeenCalledExactlyOnceWith(table);
	});
	it("selects without navigating in selection mode", async () => {
		const { user, onToggle, onOpen } = renderList(true);
		expect(screen.queryByRole("link")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Customers" }));
		expect(onToggle).toHaveBeenCalledExactlyOnceWith("customers");
		expect(onOpen).not.toHaveBeenCalled();
	});
	it("disables opening while busy", () => {
		renderList(false, true);
		expect(screen.queryByRole("link")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Customers" }),
		).toBeDisabled();
	});
});
