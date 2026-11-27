import { expect, it, vi } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";
import {
	DropdownMenu,
	DropdownMenuTrigger,
	DropdownMenuContent,
	DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import { AccountMenuSub } from "./AccountMenuSub";

it("keeps the submenu open when the pointer moves directly from its trigger into an option", async () => {
	const select = vi.fn();
	const { user } = renderWithProviders(
		<DropdownMenu>
			<DropdownMenuTrigger>Account</DropdownMenuTrigger>
			<DropdownMenuContent>
				<AccountMenuSub label="Tools" icon={null}>
					<DropdownMenuItem onSelect={select}>Tool</DropdownMenuItem>
				</AccountMenuSub>
			</DropdownMenuContent>
		</DropdownMenu>,
	);
	await user.click(screen.getByRole("button", { name: "Account" }));
	const trigger = screen.getByRole("menuitem", { name: "Tools" });
	await user.click(trigger);
	const option = screen.getByRole("menuitem", { name: "Tool" });
	fireEvent.pointerLeave(trigger, {
		relatedTarget: option,
		pointerType: "mouse",
	});
	expect(option).toBeVisible();
	option.focus();
	await user.keyboard("{Enter}");
	expect(select).toHaveBeenCalledOnce();
});
