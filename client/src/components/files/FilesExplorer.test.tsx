import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/hooks/useOrganizations", () => ({ useOrganizations: vi.fn() }));
vi.mock("@/hooks/useMediaQuery", () => ({ useMediaQuery: vi.fn() }));
vi.mock("@/services/authorization", () => ({ useAuthorization: vi.fn() }));
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
const shareTreeSolutions: Array<string | undefined> = [];
const shareTreeReadOnly: boolean[] = [];
const shareTreePolicyReadOnly: boolean[] = [];
vi.mock("./ShareTree", () => ({
	ShareTree: ({
		scope,
		solution,
		readOnly,
		policyReadOnly,
		onSelect,
		onContextAction,
	}: {
		scope: string | null;
		solution?: string;
		readOnly?: boolean;
		policyReadOnly?: boolean;
		onSelect: (location: string, prefix: string) => void;
		onContextAction: (
			action: string,
			location: string,
			prefix: string,
		) => void;
	}) => {
		shareTreeScopes.push(scope);
		shareTreeSolutions.push(solution);
		shareTreeReadOnly.push(Boolean(readOnly));
		shareTreePolicyReadOnly.push(Boolean(policyReadOnly));
		return (
			<div data-testid="share-tree">
				<button type="button" onClick={() => onSelect("gallery", "")}>
					select-gallery
				</button>
				<button
					onClick={() =>
						onContextAction("newPolicy", "gallery", "photos")
					}
				>
					New folder policy
				</button>
			</div>
		);
	},
}));
const folderListingLocations: Array<string | null> = [];
const folderListingReadOnly: boolean[] = [];
const folderListingPolicyReadOnly: boolean[] = [];
vi.mock("./FolderListing", () => ({
	FolderListing: ({
		location,
		readOnly,
		policyReadOnly,
		onSelectFile,
	}: {
		location: string | null;
		readOnly: boolean;
		policyReadOnly?: boolean;
		onSelectFile: (path: string) => void;
	}) => {
		folderListingLocations.push(location);
		folderListingReadOnly.push(readOnly);
		folderListingPolicyReadOnly.push(Boolean(policyReadOnly));
		return (
			<div data-testid="folder-listing">
				<button onClick={() => onSelectFile("notes.txt")}>
					Open notes
				</button>
			</div>
		);
	},
}));
vi.mock("./SharesOverview", () => ({
	SharesOverview: ({
		onSelect,
	}: {
		onSelect: (location: string, prefix: string) => void;
	}) => (
		<button onClick={() => onSelect("gallery", "")}>
			Open gallery share
		</button>
	),
}));
vi.mock("./FilePreview", () => ({ FilePreview: () => <div /> }));
const effectiveAccessReadOnly: boolean[] = [];
vi.mock("./EffectiveAccessPanel", () => ({
	EffectiveAccessPanel: ({ readOnly }: { readOnly?: boolean }) => {
		effectiveAccessReadOnly.push(Boolean(readOnly));
		return <div />;
	},
}));
vi.mock("./TestAccessModal", () => ({
	TestAccessPanel: () => <section aria-label="Access tester" />,
}));
vi.mock("./PolicyEditorModal", () => ({
	PolicyEditorPanel: ({ exactPath }: { exactPath?: string }) => (
		<section aria-label="Policy editor">{exactPath}</section>
	),
}));
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
import { useAuthorization } from "@/services/authorization";
import { FilesExplorer } from "./FilesExplorer";

describe("FilesExplorer", () => {
	beforeEach(() => {
		shareTreeScopes.length = 0;
		shareTreeSolutions.length = 0;
		shareTreeReadOnly.length = 0;
		shareTreePolicyReadOnly.length = 0;
		folderListingLocations.length = 0;
		folderListingReadOnly.length = 0;
		folderListingPolicyReadOnly.length = 0;
		effectiveAccessReadOnly.length = 0;
		vi.mocked(useAuth).mockReturnValue({
			isPlatformAdmin: true,
		} as ReturnType<typeof useAuth>);
		vi.mocked(useOrganizations).mockReturnValue({
			data: [{ id: "org-1", name: "Acme" }],
		} as ReturnType<typeof useOrganizations>);
		vi.mocked(useAuthorization).mockReturnValue({
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
			isPlatformAdmin: false,
			meets: () => false,
			authorization: {
				is_platform_admin: false,
				provider_organization_id: "provider",
				base_role: { id: "base", name: "User" },
				home_organization_id: "org-1",
				grants: [],
			},
			canAt: () => true,
			canAnywhere: () => true,
		});
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
		expect(
			screen.getByRole("button", { name: "Open gallery share" }),
		).toBeInTheDocument();
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});

	it("opens desktop details on demand and returns the space when closed", async () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		const user = userEvent.setup();
		render(<FilesExplorer />);
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Open gallery share" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Folder Details" }),
		);
		expect(screen.getByRole("tab", { name: "Access" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		await user.keyboard("{Escape}");
		expect(
			screen.getByRole("button", { name: "Folder Details" }),
		).toHaveFocus();
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Open notes" }));
		expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
	});

	it("gives mobile preview and access their own keyboard-switchable panels", async () => {
		vi.mocked(useMediaQuery).mockReturnValue(false);
		const user = userEvent.setup();
		render(<FilesExplorer />);
		await user.click(
			screen.getByRole("button", { name: "Open gallery share" }),
		);
		await user.click(screen.getByRole("button", { name: "Open notes" }));
		const preview = screen.getByRole("tab", { name: "Preview" });
		expect(preview).toHaveAttribute("aria-selected", "true");
		preview.focus();
		await user.keyboard("{ArrowRight}");
		expect(screen.getByRole("tab", { name: "Access" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
	});

	it("opens the policy editor directly on Access and testing on Test", async () => {
		const user = userEvent.setup();
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);
		await user.click(
			screen.getByRole("button", { name: "Open gallery share" }),
		);
		await user.click(screen.getByRole("button", { name: "Open notes" }));
		await user.click(screen.getByRole("tab", { name: "Access" }));
		expect(
			screen.getByRole("region", { name: "Policy editor" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Manage Policy" }),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: "Test" }));
		expect(
			screen.getByRole("region", { name: "Access tester" }),
		).toBeInTheDocument();
	});

	it("keeps the scope selector and breadcrumb in a shrinkable header region", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);

		const scopeSelector = screen.getByText("scope-select").parentElement;
		expect(scopeSelector).toHaveClass("min-w-0", "sm:w-[17rem]");
		expect(
			screen.getByRole("navigation", { name: /breadcrumb/i })
				.parentElement,
		).toHaveClass("min-w-0", "flex-1");
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

	it("keeps data actions available while a named read-only role cannot edit policies", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		vi.mocked(useAuthorization).mockReturnValue({
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
			isPlatformAdmin: false,
			meets: () => false,
			authorization: {
				is_platform_admin: false,
				provider_organization_id: "provider",
				base_role: { id: "base", name: "User" },
				home_organization_id: "org-1",
				grants: [],
			},
			canAt: (permission: string) => permission === "filepolicies.read",
			canAnywhere: () => true,
		});

		render(<FilesExplorer />);

		expect(
			screen.queryByRole("button", { name: "New Share" }),
		).not.toBeInTheDocument();
		expect(shareTreePolicyReadOnly).toContain(true);
		fireEvent.click(screen.getByText("select-gallery"));
		expect(folderListingReadOnly).toContain(false);
		expect(folderListingPolicyReadOnly).toContain(true);
		expect(
			screen.getByRole("button", { name: /upload/i }),
		).toBeInTheDocument();
	});

	it("does not fetch a forbidden global scope before authorization selects an allowed organization", () => {
		vi.mocked(useMediaQuery).mockReturnValue(true);
		vi.mocked(useAuthorization).mockReturnValue({
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
			isPlatformAdmin: false,
			meets: () => false,
			authorization: {
				is_platform_admin: false,
				provider_organization_id: "provider",
				base_role: { id: "base", name: "User" },
				home_organization_id: "org-1",
				grants: [],
			},
			canAt: (
				_permission: string,
				target: { kind: string; id?: string },
			) => target.kind === "org" && target.id === "org-1",
			canAnywhere: () => true,
		});

		render(<FilesExplorer />);

		expect(shareTreeScopes).toContain("org-1");
		expect(shareTreeScopes).not.toContain("global");
	});

	it("attaches a new folder policy to its folder boundary rather than its parent", async () => {
		const user = userEvent.setup();
		vi.mocked(useMediaQuery).mockReturnValue(true);
		render(<FilesExplorer />);
		await user.click(
			screen.getByRole("button", { name: "New folder policy" }),
		);
		expect(
			screen.getByRole("region", { name: "Policy editor" }),
		).toHaveTextContent("photos/");
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
		expect(
			screen.getByRole("button", { name: "Open gallery share" }),
		).toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: /policies/i }));
		expect(await screen.findByTestId("policies-view")).toBeInTheDocument();
		expect(screen.queryByTestId("folder-listing")).not.toBeInTheDocument();
		await user.click(screen.getByText("select-gallery"));
		expect(screen.getByRole("tab", { name: /policies/i })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		expect(screen.getByTestId("policies-view")).toBeInTheDocument();
	});

	it("exposes a hamburger to reach the tree on narrow screens", () => {
		vi.mocked(useMediaQuery).mockReturnValue(false); // narrow
		render(<FilesExplorer />);
		expect(
			screen.getByRole("button", { name: /open shares/i }),
		).toBeInTheDocument();
	});

	describe("install prop (solution-scoped mode)", () => {
		it("keeps ordinary scope grammar and passes the install as explicit solution context", () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			render(
				<MemoryRouter>
					<FilesExplorer install="sol-abc" />
				</MemoryRouter>,
			);
			// Solution IDs are never reinterpreted as organization scopes.
			expect(shareTreeScopes).toContain("global");
			expect(shareTreeSolutions).toContain("sol-abc");
		});

		it("makes the solution-scoped file browser read-only", () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			render(
				<MemoryRouter>
					<FilesExplorer install="sol-abc" />
				</MemoryRouter>,
			);

			expect(shareTreeReadOnly).toContain(true);
			fireEvent.click(
				screen.getByRole("button", { name: "Open gallery share" }),
			);
			expect(folderListingReadOnly).toContain(true);
			fireEvent.click(
				screen.getByRole("button", { name: "Folder Details" }),
			);
			expect(effectiveAccessReadOnly).toContain(true);
			expect(
				screen.queryByRole("button", { name: "New Share" }),
			).not.toBeInTheDocument();
			expect(
				screen.queryByRole("tab", { name: /policies/i }),
			).not.toBeInTheDocument();
			expect(
				screen.queryByRole("button", { name: /upload/i }),
			).not.toBeInTheDocument();
		});

		it("starts at the solution shares root instead of a pseudo solutions location", () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			folderListingLocations.length = 0;
			render(
				<MemoryRouter>
					<FilesExplorer
						install="sol-abc"
						installName="Finance Ops"
					/>
				</MemoryRouter>,
			);
			expect(
				screen.getByRole("button", { name: "Open gallery share" }),
			).toBeInTheDocument();
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
					<FilesExplorer
						install="sol-abc"
						installName="Finance Ops"
					/>
				</MemoryRouter>,
			);
			const back = screen.getByRole("link", {
				name: /back to solution/i,
			});
			expect(back).toHaveAttribute("href", "/solutions/sol-abc");
			expect(back).toHaveAccessibleName("Back to Solution");
			expect(screen.getByText("Finance Ops")).toHaveClass(
				"font-semibold",
			);
		});

		it("hides solution page chrome when embedded", () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			render(
				<MemoryRouter>
					<FilesExplorer
						install="sol-abc"
						installName="Finance Ops"
						embedded
					/>
				</MemoryRouter>,
			);

			expect(
				screen.queryByRole("link", { name: /back to solution/i }),
			).not.toBeInTheDocument();
			expect(screen.queryByText("Finance Ops")).not.toBeInTheDocument();
			expect(shareTreeSolutions).toContain("sol-abc");
			expect(shareTreeScopes).not.toContain("sol-abc");
			expect(shareTreeReadOnly).toContain(true);
		});

		it("shows the selected solution file location as the breadcrumb root", async () => {
			vi.mocked(useMediaQuery).mockReturnValue(true);
			const user = userEvent.setup();
			folderListingLocations.length = 0;
			render(
				<MemoryRouter>
					<FilesExplorer
						install="sol-abc"
						installName="Finance Ops"
					/>
				</MemoryRouter>,
			);

			await user.click(
				screen.getByRole("button", { name: "select-gallery" }),
			);

			expect(folderListingLocations).toContain("gallery");
			expect(
				screen.getByRole("navigation", { name: /breadcrumb/i }),
			).toHaveTextContent("gallery");
			expect(screen.queryByText("solutions")).not.toBeInTheDocument();
		});
	});
});
