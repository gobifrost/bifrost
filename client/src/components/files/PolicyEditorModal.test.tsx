import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/filePolicies", () => ({
	listFilePolicies: vi.fn(),
	saveFilePolicy: vi.fn(),
	deleteFilePolicy: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import {
	listFilePolicies,
	saveFilePolicy,
} from "@/services/filePolicies";
import { PolicyEditorModal } from "./PolicyEditorModal";

describe("PolicyEditorModal", () => {
	beforeEach(() => {
		vi.mocked(listFilePolicies).mockReset();
		vi.mocked(saveFilePolicy).mockReset();
	});

	it("loads the best policy and saves edits with the right scope/location", async () => {
		vi.mocked(listFilePolicies).mockResolvedValue({
			policies: [
				{
					id: "p1",
					location: "gallery",
					path: "",
					organizationId: null,
					policies: { policies: [] },
				},
			],
		});
		vi.mocked(saveFilePolicy).mockResolvedValue({
			id: "p1",
			location: "gallery",
			path: "",
			organizationId: null,
			policies: { policies: [] },
		});
		const onSaved = vi.fn();
		render(
			<PolicyEditorModal
				open
				onOpenChange={vi.fn()}
				location="gallery"
				scope={null}
				path="pic.png"
				onSaved={onSaved}
			/>,
		);
		// Editor renders once the best policy resolves.
		await waitFor(() =>
			expect(screen.getByText(/policy editor/i)).toBeInTheDocument(),
		);
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		await waitFor(() => expect(saveFilePolicy).toHaveBeenCalled());
		const saved = vi.mocked(saveFilePolicy).mock.calls[0][0];
		expect(saved.location).toBe("gallery");
		expect(saved.organizationId).toBeNull();
		await waitFor(() => expect(onSaved).toHaveBeenCalled());
	});
});
