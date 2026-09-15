import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import { Button } from "@/components/ui/button";
import { ResourceCatalogCard } from "./ResourceCatalogCard";

describe("ResourceCatalogCard", () => {
	it("provides a native link and leaves modified clicks to the browser", () => {
		const onOpen = vi.fn();
		renderWithProviders(
			<ResourceCatalogCard
				icon={null}
				title="Linked resource"
				href="/forms/123"
				onOpen={onOpen}
			/>,
		);
		const link = screen.getByRole("link", { name: "Linked resource" });
		expect(link).toHaveAttribute("href", "/forms/123");
		expect(fireEvent.click(link, { ctrlKey: true })).toBe(true);
		expect(fireEvent.click(link, { metaKey: true })).toBe(true);
		expect(fireEvent.click(link, { shiftKey: true })).toBe(true);
		expect(
			fireEvent(
				link,
				new MouseEvent("auxclick", {
					button: 1,
					bubbles: true,
					cancelable: true,
				}),
			),
		).toBe(true);
		expect(onOpen).not.toHaveBeenCalled();
		fireEvent.click(link);
		expect(onOpen).toHaveBeenCalledOnce();
	});

	it("opens from the title-sized card target and keeps secondary actions isolated", async () => {
		const onOpen = vi.fn();
		const onAction = vi.fn();
		const { user } = renderWithProviders(
			<ResourceCatalogCard
				icon={<span aria-hidden="true" />}
				title="Dispatch Intake"
				subtitle="Form"
				description="Coordinate field work"
				action={
					<Button
						type="button"
						variant="ghost"
						size="icon"
						aria-label="Pin Dispatch Intake"
						onClick={onAction}
					/>
				}
				footer={<span>Global</span>}
				onOpen={onOpen}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: "Dispatch Intake" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Pin Dispatch Intake" }),
		);

		expect(onOpen).toHaveBeenCalledOnce();
		expect(onAction).toHaveBeenCalledOnce();
	});

	it("disables the primary open target without disabling secondary actions", async () => {
		const onOpen = vi.fn();
		const onAction = vi.fn();
		const { user } = renderWithProviders(
			<ResourceCatalogCard
				icon={<span aria-hidden="true" />}
				title="Inactive Form"
				href="/forms/inactive"
				action={
					<Button
						type="button"
						variant="ghost"
						size="icon"
						aria-label="Inactive Form actions"
						onClick={onAction}
					/>
				}
				onOpen={onOpen}
				disabled
			/>,
		);

		expect(
			screen.getByRole("button", { name: "Inactive Form" }),
		).toBeDisabled();
		await user.click(
			screen.getByRole("button", { name: "Inactive Form actions" }),
		);

		expect(onOpen).not.toHaveBeenCalled();
		expect(onAction).toHaveBeenCalledOnce();
	});
});
