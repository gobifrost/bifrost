import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import type { HomeCollection, HomeResource } from "@/services/home";
import { CollectionEditor } from "./CollectionEditor";

const authority = vi.hoisted(() => ({ shared: false, scoped: false }));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: { home_organization_id: "org-1" },
		canAnywhere: (permission: string) =>
			permission === "home.readwrite" && authority.shared,
		canAt: (_permission: string, target: { kind: string; id?: string }) =>
			authority.shared && (!authority.scoped || target.id === "org-1"),
	}),
}));
beforeEach(() => {
	authority.shared = false;
	authority.scoped = false;
});

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [{ id: "org-1", name: "Contoso" }] }),
}));

const resources: HomeResource[] = [
	{
		key: "app:dispatch",
		id: "app-1",
		kind: "app",
		name: "Dispatch Board",
		description: "Coordinate field work",
		icon: "app-window",
		organization_id: null,
		organization_name: "Global",
		href: "/apps/dispatch",
		pinned: false,
		last_opened_at: null,
	},
	{
		key: "form:intake",
		id: "form-1",
		kind: "form",
		name: "Intake Form",
		description: "Collect requests",
		icon: "file-input",
		organization_id: "org-1",
		organization_name: "Contoso",
		href: "/forms/form-1/start",
		pinned: false,
		last_opened_at: null,
	},
	{
		key: "agent:triage",
		id: "agent-1",
		kind: "agent",
		name: "Triage Agent",
		description: "Sort tickets",
		icon: "bot",
		organization_id: "org-2",
		organization_name: "Fabrikam",
		href: "/agents/agent-1",
		pinned: false,
		last_opened_at: null,
	},
];

const collection: HomeCollection = {
	id: "col-1",
	name: "Daily work",
	description: "Things to open first",
	icon: "folder",
	shared: false,
	organization_id: null,
	resource_keys: ["app:dispatch", "form:intake"],
	can_edit: true,
	organization_name: null,
};

describe("CollectionEditor", () => {
	it("offers a named scoped writer its known organization without Global or organization discovery", async () => {
		authority.shared = true;
		authority.scoped = true;
		const onSave = vi.fn();
		const { user } = renderWithProviders(
			<CollectionEditor
				resources={resources}
				onClose={vi.fn()}
				onSave={onSave}
				onDelete={vi.fn()}
				busy={false}
				error=""
			/>,
		);
		await user.type(screen.getByLabelText("Name"), "Contoso shared work");
		await user.click(
			screen.getByRole("combobox", { name: /who is this for/i }),
		);
		expect(
			screen.queryByRole("option", { name: /^Global/ }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("option", { name: "Fabrikam" }),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("option", { name: "Contoso" }));
		await user.click(
			screen.getByRole("button", { name: "Save collection" }),
		);
		expect(onSave).toHaveBeenCalledWith(
			expect.objectContaining({ shared: true, organization_id: "org-1" }),
		);
	});

	it("creates a personal collection with searched resources, icon, and selected order", async () => {
		const onSave = vi.fn();
		const { user } = renderWithProviders(
			<CollectionEditor
				collection={undefined}
				resources={resources}
				onClose={vi.fn()}
				onSave={onSave}
				onDelete={vi.fn()}
				busy={false}
				error=""
			/>,
		);

		await user.type(screen.getByLabelText("Name"), "Launch kit");
		await user.type(
			screen.getByLabelText("Description"),
			"Morning shortcuts",
		);
		await user.type(
			screen.getByLabelText("Search collection icons"),
			"rocket",
		);
		await user.click(screen.getByRole("button", { name: "rocket" }));
		await user.type(
			screen.getByLabelText("Search collection resources"),
			"dispatch",
		);
		await user.click(
			screen.getByRole("checkbox", { name: /Dispatch Board/ }),
		);
		await user.clear(screen.getByLabelText("Search collection resources"));
		await user.type(
			screen.getByLabelText("Search collection resources"),
			"intake",
		);
		await user.click(screen.getByRole("checkbox", { name: /Intake Form/ }));
		await user.click(
			screen.getByRole("button", { name: "Move Intake Form up" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Save collection" }),
		);

		expect(onSave).toHaveBeenCalledWith({
			name: "Launch kit",
			description: "Morning shortcuts",
			icon: "rocket",
			shared: false,
			organization_id: null,
			resource_keys: ["form:intake", "app:dispatch"],
		});
	});

	it("filters selectable resources by permitted audience and confirms deletion", async () => {
		const onSave = vi.fn();
		const onDelete = vi.fn();
		authority.shared = true;
		const { user } = renderWithProviders(
			<CollectionEditor
				collection={collection}
				resources={resources}
				onClose={vi.fn()}
				onSave={onSave}
				onDelete={onDelete}
				busy={false}
				error=""
			/>,
		);

		await user.click(
			screen.getByRole("combobox", { name: /who is this for/i }),
		);
		await user.click(screen.getByRole("option", { name: "Contoso" }));
		const addResources = screen.getByRole("group", {
			name: "Add resources",
		});
		expect(
			within(addResources).getByText("Dispatch Board"),
		).toBeInTheDocument();
		expect(
			within(addResources).getByText("Intake Form"),
		).toBeInTheDocument();
		expect(
			within(addResources).queryByText("Triage Agent"),
		).not.toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Save collection" }),
		);
		expect(onSave).toHaveBeenCalledWith(
			expect.objectContaining({
				shared: true,
				organization_id: "org-1",
				resource_keys: ["app:dispatch", "form:intake"],
			}),
		);

		await user.click(
			screen.getByRole("combobox", { name: /who is this for/i }),
		);
		await user.click(screen.getByRole("option", { name: /Only me/ }));
		await user.click(
			screen.getByRole("button", { name: "Save collection" }),
		);
		expect(onSave).toHaveBeenLastCalledWith(
			expect.objectContaining({ shared: false, organization_id: null }),
		);

		await user.click(screen.getByRole("button", { name: "Delete" }));
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Delete this collection?",
		);
		await user.click(
			screen.getByRole("button", { name: "Confirm delete" }),
		);
		expect(onDelete).toHaveBeenCalledOnce();
	});
});
