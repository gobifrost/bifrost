import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import {
	DropdownMenu,
	DropdownMenuTrigger,
	DropdownMenuContent,
} from "@/components/ui/dropdown-menu";
import { VersionMenuItem } from "./VersionMenuItem";
const copy = vi.hoisted(() => vi.fn());
vi.mock("@/lib/clipboard", () => ({
	copyToClipboard: (text: string) => copy(text),
}));
it("reports clipboard failure without claiming success and allows retry", async () => {
	copy.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
	const user = userEvent.setup();
	render(
		<DropdownMenu>
			<DropdownMenuTrigger>Account</DropdownMenuTrigger>
			<DropdownMenuContent>
				<VersionMenuItem />
			</DropdownMenuContent>
		</DropdownMenu>,
	);
	await user.click(screen.getByRole("button", { name: "Account" }));
	await user.click(screen.getByRole("menuitem", { name: /Copy version/ }));
	expect(await screen.findByText("Copy failed. Try again.")).toBeVisible();
	expect(screen.queryByText("Copied!")).not.toBeInTheDocument();
	await user.click(screen.getByRole("menuitem", { name: /Copy version/ }));
	await waitFor(() => expect(screen.getByText("Copied!")).toBeVisible());
	expect(copy).toHaveBeenCalledTimes(2);
	expect(copy.mock.calls[0]).toEqual(copy.mock.calls[1]);
});
