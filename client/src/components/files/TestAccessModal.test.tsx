import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/hooks/useUsers", () => ({
	useUsersFiltered: vi.fn(),
}));
vi.mock("@/services/filePolicies", () => ({
	testAllActions: vi.fn(),
}));
import { useUsersFiltered } from "@/hooks/useUsers";
import { testAllActions } from "@/services/filePolicies";
import { TestAccessModal } from "./TestAccessModal";

function result(action: string, allowed: boolean) {
	return { allowed, path: "p", location: "gallery", action };
}

describe("TestAccessModal", () => {
	beforeEach(() => {
		vi.mocked(useUsersFiltered).mockReturnValue({
			data: [{ id: "u1", email: "alice@x.com", name: "Alice" }],
		} as ReturnType<typeof useUsersFiltered>);
		vi.mocked(testAllActions).mockResolvedValue({
			read: result("read", true),
			write: result("write", false),
			delete: result("delete", false),
			list: result("list", true),
		});
	});

	it("resolves four per-action results after picking a user", async () => {
		render(
			<TestAccessModal
				open
				onOpenChange={vi.fn()}
				location="gallery"
				scope={null}
				path="pic.png"
			/>,
		);
		// Open the combobox and select the user.
		fireEvent.click(screen.getByRole("combobox"));
		fireEvent.click(await screen.findByText(/Alice/));

		await waitFor(() =>
			expect(testAllActions).toHaveBeenCalledWith({
				location: "gallery",
				path: "pic.png",
				scope: null,
				userId: "u1",
			}),
		);
		// Four action rows render with allowed/denied badges.
		expect(await screen.findByText("read")).toBeInTheDocument();
		expect(screen.getByText("write")).toBeInTheDocument();
		expect(screen.getAllByText("Allowed").length).toBe(2);
		expect(screen.getAllByText("Denied").length).toBe(2);
	});
});
