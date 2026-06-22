import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/filePolicies", () => ({
	effectiveAccess: vi.fn(),
}));
import { effectiveAccess } from "@/services/filePolicies";
import { EffectiveAccessPanel } from "./EffectiveAccessPanel";

describe("EffectiveAccessPanel", () => {
	beforeEach(() => vi.mocked(effectiveAccess).mockReset());

	it("renders the resolved cascade with the longest-prefix one winning", async () => {
		vi.mocked(effectiveAccess).mockResolvedValue([
			{
				id: "2",
				location: "gallery",
				path: "team/",
				policies: { policies: [{ name: "team-rule", actions: ["read"] }] },
			},
			{
				id: "1",
				location: "gallery",
				path: "",
				policies: { policies: [{ name: "root-rule", actions: ["read", "list"] }] },
			},
		]);
		render(
			<EffectiveAccessPanel
				location="gallery"
				scope={null}
				path="team/pic.png"
				onOpenTest={vi.fn()}
				onManagePolicy={vi.fn()}
			/>,
		);
		await waitFor(() =>
			expect(screen.getByText("team-rule")).toBeInTheDocument(),
		);
		expect(screen.getByText("root-rule")).toBeInTheDocument();
		// The winning badge sits in the first (longest-prefix) policy card.
		expect(screen.getByText("winning")).toBeInTheDocument();
	});

	it("fires onOpenTest when Test access is clicked", async () => {
		vi.mocked(effectiveAccess).mockResolvedValue([]);
		const onOpenTest = vi.fn();
		render(
			<EffectiveAccessPanel
				location="gallery"
				scope={null}
				path="pic.png"
				onOpenTest={onOpenTest}
				onManagePolicy={vi.fn()}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: /test access/i }));
		expect(onOpenTest).toHaveBeenCalled();
	});
});
