import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
	DropdownMenu,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AccountMenuContent } from "./AccountMenuContent";
it("keeps account actions separate from admin help and version", async () => {
	const settings = vi.fn();
	const user = userEvent.setup();
	render(
		<DropdownMenu>
			<DropdownMenuTrigger>Account</DropdownMenuTrigger>
			<AccountMenuContent
				name="Fixture"
				email="fixture@example.test"
				initials="F"
				onSettings={settings}
				onLogout={vi.fn()}
				showVersion={false}
			/>
		</DropdownMenu>,
	);
	await user.click(screen.getByRole("button", { name: "Account" }));
	expect(screen.getByText("fixture@example.test")).toBeVisible();
	expect(
		screen.queryByRole("menuitem", { name: /Copy version/ }),
	).not.toBeInTheDocument();
	await user.click(
		screen.getByRole("menuitem", { name: "Settings" }),
	);
	expect(settings).toHaveBeenCalledOnce();
});
