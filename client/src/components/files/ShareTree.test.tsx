import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/fileStructure", () => ({
	listShares: vi.fn(),
	listStructure: vi.fn(),
}));
import { listShares, listStructure } from "@/services/fileStructure";
import { ShareTree } from "./ShareTree";

describe("ShareTree", () => {
	beforeEach(() => {
		vi.mocked(listShares).mockResolvedValue([
			{ location: "gallery", readOnly: false, hasPolicy: true },
			{ location: "uploads", readOnly: true, hasPolicy: false },
		]);
		vi.mocked(listStructure).mockResolvedValue([
			{ name: "team", kind: "folder", path: "team" },
		]);
	});

	it("lists shares and marks uploads read-only", async () => {
		render(
			<ShareTree
				scope={null}
				selectedLocation={null}
				selectedPrefix=""
				onSelect={vi.fn()}
				onContextAction={vi.fn()}
			/>,
		);
		expect(await screen.findByText("gallery")).toBeInTheDocument();
		expect(screen.getByText("uploads")).toBeInTheDocument();
		expect(screen.getByText(/read-only/i)).toBeInTheDocument();
	});

	it("selects a share on click and lazy-loads its folders", async () => {
		const onSelect = vi.fn();
		render(
			<ShareTree
				scope={null}
				selectedLocation="gallery"
				selectedPrefix=""
				onSelect={onSelect}
				onContextAction={vi.fn()}
			/>,
		);
		fireEvent.click(await screen.findByText("gallery"));
		expect(onSelect).toHaveBeenCalledWith("gallery", "");
		await waitFor(() =>
			expect(listStructure).toHaveBeenCalledWith("gallery", "", null),
		);
		expect(await screen.findByText("team")).toBeInTheDocument();
	});
});
