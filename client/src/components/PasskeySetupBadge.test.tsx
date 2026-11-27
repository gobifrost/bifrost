import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import {
	DropdownMenu,
	DropdownMenuTrigger,
	DropdownMenuContent,
} from "@/components/ui/dropdown-menu";
import { PasskeySetupBadge } from "./PasskeySetupBadge";

const mockUsePasskeyList = vi.fn();

vi.mock("@/hooks/usePasskeys", () => ({
	usePasskeyList: () => mockUsePasskeyList(),
}));

vi.mock("@/services/passkeys", () => ({
	supportsPasskeys: () => true,
}));

beforeEach(() => {
	localStorage.clear();
	mockUsePasskeyList.mockReturnValue({
		data: { count: 0, passkeys: [] },
		isLoading: false,
	});
});

async function renderMenu() {
	const view = renderWithProviders(
		<DropdownMenu>
			<DropdownMenuTrigger>Account</DropdownMenuTrigger>
			<DropdownMenuContent>
				<PasskeySetupBadge />
			</DropdownMenuContent>
		</DropdownMenu>,
	);
	await view.user.click(screen.getByRole("button", { name: "Account" }));
	return view;
}

describe("PasskeySetupBadge", () => {
	it("renders an account menu action for users who can add a passkey", async () => {
		await renderMenu();

		expect(
			screen.getByRole("menuitem", { name: /set up passkey/i }),
		).toBeInTheDocument();
		expect(
			document.querySelector("[data-slot='passkey-setup-indicator']"),
		).toHaveClass("bg-primary");
		expect(
			screen.queryByText(/add a passkey to sign in faster/i),
		).not.toBeInTheDocument();
	});

	it("stays hidden when the user already has a passkey", async () => {
		mockUsePasskeyList.mockReturnValue({
			data: { count: 1, passkeys: [{ id: "passkey-1" }] },
			isLoading: false,
		});

		await renderMenu();

		expect(
			screen.queryByRole("menuitem", { name: /set up passkey/i }),
		).not.toBeInTheDocument();
	});

	it("does not crash when localStorage is unavailable", async () => {
		const storageSpy = vi
			.spyOn(Storage.prototype, "getItem")
			.mockImplementation(() => {
				throw new Error("storage denied");
			});

		try {
			await renderMenu();

			expect(
				screen.getByRole("menuitem", { name: /set up passkey/i }),
			).toBeInTheDocument();
		} finally {
			storageSpy.mockRestore();
		}
	});
});
