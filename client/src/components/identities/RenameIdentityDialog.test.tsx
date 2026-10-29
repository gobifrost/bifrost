import { useRef, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

import { RenameIdentityDialog } from "./RenameIdentityDialog";

const rename = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/services/identities", () => ({
	useRenameIdentity: () => rename,
}));

function Harness() {
	const trigger = useRef<HTMLButtonElement>(null);
	const [open, setOpen] = useState(false);
	return (
		<>
			<button ref={trigger} type="button" onClick={() => setOpen(true)}>
				Contoso Billing Sync actions
			</button>
			<RenameIdentityDialog
				identity={{ id: "identity-1", name: "Contoso Billing Sync" }}
				open={open}
				onOpenChange={setOpen}
				returnFocusRef={trigger}
			/>
		</>
	);
}

beforeEach(() => {
	rename.mutateAsync.mockReset();
	rename.isPending = false;
});

describe("RenameIdentityDialog", () => {
	it("returns focus to the actions button when it closes", async () => {
		const { user } = renderWithProviders(<Harness />);
		const opener = screen.getByRole("button", {
			name: "Contoso Billing Sync actions",
		});

		await user.click(opener);
		await user.click(screen.getByRole("button", { name: "Cancel" }));

		await waitFor(() => expect(opener).toHaveFocus());
	});

	it("can't be dismissed while the rename is saving", async () => {
		rename.isPending = true;
		const { user } = renderWithProviders(<Harness />);

		await user.click(
			screen.getByRole("button", {
				name: "Contoso Billing Sync actions",
			}),
		);

		expect(screen.getByRole("dialog")).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Close" }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
	});
});
