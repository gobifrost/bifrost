import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/filePolicies", () => ({ saveFilePolicy: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { saveFilePolicy } from "@/services/filePolicies";
import { NewShareDialog } from "./NewShareDialog";

describe("NewShareDialog", () => {
	beforeEach(() => vi.mocked(saveFilePolicy).mockReset());

	it("rejects a reserved name without calling the API", async () => {
		render(
			<NewShareDialog open onOpenChange={vi.fn()} scope={null} onCreated={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText(/share name/i), {
			target: { value: "uploads" },
		});
		fireEvent.click(screen.getByRole("button", { name: /create share/i }));
		expect(await screen.findByText(/reserved name/i)).toBeInTheDocument();
		expect(saveFilePolicy).not.toHaveBeenCalled();
	});

	it("creates the first policy (empty doc) and reports the new share", async () => {
		vi.mocked(saveFilePolicy).mockResolvedValue({
			id: "p1",
			location: "gallery",
			path: "",
			organizationId: "org-1",
			policies: { policies: [] },
		});
		const onCreated = vi.fn();
		render(
			<NewShareDialog
				open
				onOpenChange={vi.fn()}
				scope="org-1"
				onCreated={onCreated}
			/>,
		);
		fireEvent.change(screen.getByLabelText(/share name/i), {
			target: { value: "gallery" },
		});
		fireEvent.click(screen.getByRole("button", { name: /create share/i }));
		await waitFor(() =>
			expect(saveFilePolicy).toHaveBeenCalledWith({
				location: "gallery",
				path: "",
				organizationId: "org-1",
				policies: { policies: [] },
			}),
		);
		expect(onCreated).toHaveBeenCalledWith("gallery");
	});
});
