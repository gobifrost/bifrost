import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/filePolicies", () => ({ listFilePolicies: vi.fn() }));
import { listFilePolicies } from "@/services/filePolicies";
import { PoliciesView } from "./PoliciesView";

describe("PoliciesView", () => {
	beforeEach(() => vi.mocked(listFilePolicies).mockReset());

	it("lists every policy in scope and edits one on click", async () => {
		vi.mocked(listFilePolicies).mockResolvedValue({
			policies: [
				{
					id: "p1",
					location: "gallery",
					path: "",
					organizationId: null,
					policies: { policies: [{ name: "admin_bypass", actions: ["read"] }] },
				},
				{
					id: "p2",
					location: "reports",
					path: "q1/",
					organizationId: null,
					policies: { policies: [{ name: "team", actions: ["read", "list"] }] },
				},
			],
		});
		const onEdit = vi.fn();
		render(<PoliciesView scope={null} refreshKey={0} onEdit={onEdit} onDelete={vi.fn()} />);

		expect(await screen.findByText("gallery")).toBeInTheDocument();
		expect(screen.getByText("reports")).toBeInTheDocument();
		expect(screen.getByText("admin_bypass")).toBeInTheDocument();

		fireEvent.click(screen.getByText("reports"));
		await waitFor(() =>
			expect(onEdit).toHaveBeenCalledWith(
				expect.objectContaining({ location: "reports", path: "q1/" }),
			),
		);
	});

	it("fires onDelete from the row delete button", async () => {
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
		const onDelete = vi.fn();
		render(
			<PoliciesView
				scope={null}
				refreshKey={0}
				onEdit={vi.fn()}
				onDelete={onDelete}
			/>,
		);
		fireEvent.click(
			await screen.findByRole("button", { name: /delete policy for gallery/i }),
		);
		await waitFor(() =>
			expect(onDelete).toHaveBeenCalledWith(
				expect.objectContaining({ location: "gallery" }),
			),
		);
	});

	it("shows an empty state when there are no policies", async () => {
		vi.mocked(listFilePolicies).mockResolvedValue({ policies: [] });
		render(<PoliciesView scope={null} refreshKey={0} onEdit={vi.fn()} onDelete={vi.fn()} />);
		expect(await screen.findByText(/no policies in this scope/i)).toBeInTheDocument();
	});
});
