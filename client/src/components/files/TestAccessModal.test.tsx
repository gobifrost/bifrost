import {
	renderWithProviders as render,
	screen,
	fireEvent,
	waitFor,
} from "@/test-utils";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/hooks/useUsers", () => ({
	useUsersFiltered: vi.fn(),
}));
vi.mock("@/services/filePolicies", () => ({
	testAllActions: vi.fn(),
}));
import { useUsersFiltered } from "@/hooks/useUsers";
import { testAllActions } from "@/services/filePolicies";
import type { FilePolicyAction } from "@/services/filePolicies";
import { TestAccessModal } from "./TestAccessModal";

function result(
	action: FilePolicyAction,
	allowed: boolean,
	matchedRule?: string,
) {
	return { allowed, path: "p", location: "gallery", action, matchedRule };
}

describe("TestAccessModal", () => {
	beforeEach(() => {
		vi.mocked(testAllActions).mockReset();
		vi.mocked(useUsersFiltered).mockReturnValue({
			data: [{ id: "u1", email: "alice@x.com", name: "Alice" }],
		} as ReturnType<typeof useUsersFiltered>);
		vi.mocked(testAllActions).mockResolvedValue({
			read: result("read", true, "admin_bypass"),
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
		expect(
			screen.getByText("Allowed by Administrator Access."),
		).toBeInTheDocument();
	});
	it("does not show an older user's results after the selection changes", async () => {
		vi.mocked(useUsersFiltered).mockReturnValue({
			data: [
				{ id: "u1", email: "alice@x.com", name: "Alice" },
				{ id: "u2", email: "bob@x.com", name: "Bob" },
			],
		} as ReturnType<typeof useUsersFiltered>);
		let finish!: (
			value: Awaited<ReturnType<typeof testAllActions>>,
		) => void;
		vi.mocked(testAllActions)
			.mockReturnValueOnce(
				new Promise((resolve) => {
					finish = resolve;
				}),
			)
			.mockResolvedValue({
				read: result("read", false),
				write: result("write", false),
				delete: result("delete", false),
				list: result("list", false),
			});
		render(
			<TestAccessModal
				open
				onOpenChange={vi.fn()}
				location="gallery"
				scope={null}
				path="p"
			/>,
		);
		fireEvent.click(screen.getByLabelText("User"));
		fireEvent.click(await screen.findByRole("option", { name: /Alice/ }));
		expect(await screen.findByRole("status")).toHaveTextContent(
			"Resolving access",
		);
		fireEvent.click(screen.getByLabelText("User"));
		fireEvent.click(await screen.findByRole("option", { name: /Bob/ }));
		expect(await screen.findAllByText("Denied")).toHaveLength(4);
		finish({
			read: result("read", true),
			write: result("write", true),
			delete: result("delete", true),
			list: result("list", true),
		});
		await waitFor(() =>
			expect(screen.queryByText("Allowed")).not.toBeInTheDocument(),
		);
	});

	it("retries a failed test without displaying a denial decision", async () => {
		vi.mocked(testAllActions).mockRejectedValueOnce(new Error("Offline"));
		render(
			<TestAccessModal
				open
				onOpenChange={vi.fn()}
				location="gallery"
				scope="org-1"
				path="p"
			/>,
		);
		fireEvent.click(screen.getByLabelText("User"));
		fireEvent.click(await screen.findByRole("option", { name: /Alice/ }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"No access decision was returned",
		);
		expect(screen.queryByText("Denied")).not.toBeInTheDocument();
		fireEvent.click(screen.getByRole("button", { name: "Retry Test" }));
		expect(await screen.findAllByText("Allowed")).toHaveLength(2);
		expect(testAllActions).toHaveBeenLastCalledWith({
			location: "gallery",
			scope: "org-1",
			path: "p",
			userId: "u1",
		});
	});

	it("exposes user-load retry and resets the selection for another path", async () => {
		const refetch = vi.fn();
		vi.mocked(useUsersFiltered).mockReturnValue({
			data: [],
			isError: true,
			refetch,
		} as unknown as ReturnType<typeof useUsersFiltered>);
		const props = {
			open: true,
			onOpenChange: vi.fn(),
			location: "gallery",
			scope: null,
			path: "p",
		};
		const view = render(<TestAccessModal {...props} />);
		expect(screen.getByLabelText("User")).toBeDisabled();
		fireEvent.click(screen.getByRole("button", { name: "Retry Users" }));
		expect(refetch).toHaveBeenCalledOnce();
		vi.mocked(useUsersFiltered).mockReturnValue({
			data: [{ id: "u1", email: "alice@x.com", name: "Alice" }],
		} as ReturnType<typeof useUsersFiltered>);
		view.rerender(<TestAccessModal {...props} />);
		fireEvent.click(screen.getByLabelText("User"));
		fireEvent.click(await screen.findByRole("option", { name: /Alice/ }));
		expect(await screen.findAllByText("Allowed")).toHaveLength(2);
		view.rerender(<TestAccessModal {...props} path="another.txt" />);
		expect(
			screen.queryByRole("list", { name: "Access test results" }),
		).not.toBeInTheDocument();
		expect(screen.getByLabelText("User")).toHaveTextContent(
			"Select a user",
		);
	});
});
