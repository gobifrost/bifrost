import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/contexts/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/hooks/useOrganizations", () => ({ useOrganizations: vi.fn() }));
vi.mock("@/hooks/useMediaQuery", () => ({ useMediaQuery: vi.fn() }));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		onChange,
	}: {
		onChange: (v: string | null) => void;
	}) => (
		<button type="button" onClick={() => onChange("org-1")}>
			scope-select
		</button>
	),
}));
vi.mock("./ShareTree", () => ({
	ShareTree: () => <div data-testid="share-tree" />,
}));
vi.mock("./FolderListing", () => ({
	FolderListing: () => <div data-testid="folder-listing" />,
}));
vi.mock("./FilePreview", () => ({ FilePreview: () => <div /> }));
vi.mock("./EffectiveAccessPanel", () => ({
	EffectiveAccessPanel: () => <div />,
}));
vi.mock("./TestAccessModal", () => ({ TestAccessModal: () => <div /> }));
vi.mock("./PolicyEditorModal", () => ({ PolicyEditorModal: () => <div /> }));
vi.mock("./NewShareDialog", () => ({
	NewShareDialog: ({ open }: { open: boolean }) =>
		open ? <div data-testid="new-share-dialog" /> : null,
}));

import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { FilesExplorer } from "./FilesExplorer";

describe("FilesExplorer", () => {
	beforeEach(() => {
		vi.mocked(useAuth).mockReturnValue({
			isPlatformAdmin: true,
		} as ReturnType<typeof useAuth>);
		vi.mocked(useOrganizations).mockReturnValue({
			data: [{ id: "org-1", name: "Acme" }],
		} as ReturnType<typeof useOrganizations>);
	});

	it("renders the desktop 3-pane shell with scope selector + new share", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true); // wide
		render(<FilesExplorer />);
		expect(screen.getByText("scope-select")).toBeInTheDocument();
		expect(screen.getByText("Global")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /new share/i }),
		).toBeInTheDocument();
		expect(screen.getByTestId("share-tree")).toBeInTheDocument();
		expect(screen.getByTestId("folder-listing")).toBeInTheDocument();
		expect(screen.getByTestId("detail-pane")).toBeInTheDocument();
	});

	it("opens the New share dialog", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);
		fireEvent.click(screen.getByRole("button", { name: /new share/i }));
		expect(screen.getByTestId("new-share-dialog")).toBeInTheDocument();
	});

	it("exposes a hamburger to reach the tree on narrow screens", () => {
		vi.mocked(useMediaQuery).mockReturnValue(false); // narrow
		render(<FilesExplorer />);
		expect(
			screen.getByRole("button", { name: /open shares/i }),
		).toBeInTheDocument();
	});
});
