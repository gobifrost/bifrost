import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
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
const shareTreeScopes: Array<string | null> = [];
vi.mock("./ShareTree", () => ({
	ShareTree: ({
		scope,
		onSelect,
	}: {
		scope: string | null;
		onSelect: (location: string, prefix: string) => void;
	}) => {
		shareTreeScopes.push(scope);
		return (
			<div data-testid="share-tree">
				<button type="button" onClick={() => onSelect("gallery", "")}>
					select-gallery
				</button>
			</div>
		);
	},
}));
const folderListingLocations: Array<string | null> = [];
vi.mock("./FolderListing", () => ({
	FolderListing: ({ location }: { location: string | null }) => {
		folderListingLocations.push(location);
		return <div data-testid="folder-listing" />;
	},
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
vi.mock("./PoliciesView", () => ({
	PoliciesView: () => <div data-testid="policies-view" />,
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

	it("keeps the scope selector and breadcrumb in a shrinkable header region", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);

		const scopeSelector = screen.getByText("scope-select").parentElement;
		expect(scopeSelector).toHaveClass("shrink-0");
		expect(screen.getByRole("navigation", { name: /breadcrumb/i }).parentElement)
			.toHaveClass("min-w-0", "flex-1");
	});

	it("passes the explicit 'global' scope (not null) to children at default", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		shareTreeScopes.length = 0;
		render(<FilesExplorer />);
		// Global must be the literal "global" string so write/upload and the
		// structural list resolve to the same tree (null would mean the
		// caller's own org on the write path).
		expect(shareTreeScopes).toContain("global");
		expect(shareTreeScopes).not.toContain(null);
	});

	it("opens the New share dialog", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);
		fireEvent.click(screen.getByRole("button", { name: /new share/i }));
		expect(screen.getByTestId("new-share-dialog")).toBeInTheDocument();
	});

	it("shows the header Upload button only once a writable folder is selected", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);
		// At the shares root (no location) there is nothing to upload to.
		expect(
			screen.queryByRole("button", { name: /upload/i }),
		).not.toBeInTheDocument();
		// Selecting a (writable) share reveals Upload in the header.
		fireEvent.click(screen.getByText("select-gallery"));
		expect(
			screen.getByRole("button", { name: /upload/i }),
		).toBeInTheDocument();
	});

	it("labels the New Share button with title case", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);
		expect(
			screen.getByRole("button", { name: "New Share" }),
		).toBeInTheDocument();
	});

	it("toggles to the Policies view", async () => {
		const user = userEvent.setup();
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);
		expect(screen.getByTestId("folder-listing")).toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: /policies/i }));
		expect(await screen.findByTestId("policies-view")).toBeInTheDocument();
		expect(screen.queryByTestId("folder-listing")).not.toBeInTheDocument();
	});

	it("exposes a hamburger to reach the tree on narrow screens", () => {
		vi.mocked(useMediaQuery).mockReturnValue(false); // narrow
		render(<FilesExplorer />);
		expect(
			screen.getByRole("button", { name: /open shares/i }),
		).toBeInTheDocument();
	});

	describe("install prop (solution-scoped mode)", () => {
		it("pins scope to the install id and passes it to ShareTree", () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			shareTreeScopes.length = 0;
			render(
				<MemoryRouter>
					<FilesExplorer install="sol-abc" />
				</MemoryRouter>,
			);
			// scope must be the install id, not "global" or an org id.
			expect(shareTreeScopes).toContain("sol-abc");
		});

		it("starts at the solution shares root instead of a pseudo solutions location", () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			folderListingLocations.length = 0;
			render(
				<MemoryRouter>
					<FilesExplorer install="sol-abc" installName="Finance Ops" />
				</MemoryRouter>,
			);
			expect(folderListingLocations).toContain(null);
			expect(screen.queryByText("solutions")).not.toBeInTheDocument();
		});

		it("hides the org/global selector when install is set", () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			render(
				<MemoryRouter>
					<FilesExplorer install="sol-abc" />
				</MemoryRouter>,
			);
			expect(screen.queryByText("scope-select")).not.toBeInTheDocument();
		});

		it("shows a back link to the solution detail page", () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			render(
				<MemoryRouter>
					<FilesExplorer install="sol-abc" installName="Finance Ops" />
				</MemoryRouter>,
			);
			const back = screen.getByRole("link", { name: /back to solution/i });
			expect(back).toHaveAttribute("href", "/solutions/sol-abc");
			expect(back).toHaveTextContent("Back");
			expect(screen.getByText("Finance Ops")).toHaveClass("font-semibold");
		});

		it("shows the selected solution file location as the breadcrumb root", async () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			const user = userEvent.setup();
			folderListingLocations.length = 0;
			render(
				<MemoryRouter>
					<FilesExplorer install="sol-abc" installName="Finance Ops" />
				</MemoryRouter>,
			);

			await user.click(screen.getByRole("button", { name: "select-gallery" }));

			expect(folderListingLocations).toContain("gallery");
			expect(screen.getByRole("navigation", { name: /breadcrumb/i }))
				.toHaveTextContent("gallery");
			expect(screen.queryByText("solutions")).not.toBeInTheDocument();
		});
	});
});
