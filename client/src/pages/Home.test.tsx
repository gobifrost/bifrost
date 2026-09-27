import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";
import type { HomeCollection, HomeResource } from "@/services/home";
import { Home } from "./Home";

const state = vi.hoisted(() => ({
	isPlatformAdmin: false,
	home: {
		data: undefined as
			| { resources: HomeResource[]; collections: HomeCollection[] }
			| undefined,
		isLoading: false,
		error: null as Error | null,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	},
	preference: { mutateAsync: vi.fn(), isPending: false },
	createCollection: { mutateAsync: vi.fn(), isPending: false },
	updateCollection: { mutateAsync: vi.fn(), isPending: false },
	deleteCollection: { mutateAsync: vi.fn(), isPending: false },
	invalidateQueries: vi.fn(),
	conversations: [] as Array<{
		id: string;
		title?: string | null;
		agent_name?: string | null;
	}>,
	createConversation: { mutateAsync: vi.fn() },
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: state.isPlatformAdmin }),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
	const actual =
		await importOriginal<typeof import("@tanstack/react-query")>();
	return {
		...actual,
		useQueryClient: () => ({ invalidateQueries: state.invalidateQueries }),
	};
});

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: () => state.home,
		useMutation: (method: string, path: string) => {
			if (
				method === "put" &&
				path === "/api/home/preferences/{resource_key}"
			)
				return state.preference;
			if (method === "post" && path === "/api/home/collections")
				return state.createCollection;
			if (
				method === "put" &&
				path === "/api/home/collections/{collection_id}"
			)
				return state.updateCollection;
			if (
				method === "delete" &&
				path === "/api/home/collections/{collection_id}"
			)
				return state.deleteCollection;
			throw new Error(`Unexpected mutation ${method} ${path}`);
		},
	},
}));

vi.mock("@/hooks/useChat", () => ({
	useConversations: () => ({ data: state.conversations }),
	useCreateConversation: () => state.createConversation,
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [{ id: "org-1", name: "Acme" }] }),
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
		pinned: true,
		last_opened_at: "2026-09-08T13:00:00Z",
	},
	{
		key: "form:intake",
		id: "form-1",
		kind: "form",
		name: "Intake Form",
		description: "Collect requests",
		icon: "file-input",
		organization_id: "org-1",
		organization_name: "Acme",
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
		organization_id: "org-1",
		organization_name: "Acme",
		href: "/agents/agent-1",
		pinned: false,
		last_opened_at: null,
	},
];

const collections: HomeCollection[] = [
	{
		id: "col-1",
		name: "Operations",
		description: "Daily customer work",
		icon: "briefcase-business",
		shared: false,
		organization_id: null,
		resource_keys: ["form:intake", "app:dispatch"],
		can_edit: true,
		organization_name: null,
	},
];

function LocationProbe() {
	const location = useLocation();
	return (
		<div data-testid="location-probe">
			{location.pathname + location.search}
		</div>
	);
}

function renderHome(initialEntries = ["/"]) {
	return renderWithProviders(
		<Routes>
			<Route
				path="*"
				element={
					<>
						<Home />
						<LocationProbe />
					</>
				}
			/>
		</Routes>,
		{ initialEntries },
	);
}

beforeEach(() => {
	vi.clearAllMocks();
	state.isPlatformAdmin = false;
	Object.assign(state.home, {
		data: { resources, collections },
		isLoading: false,
		error: null,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	state.preference = {
		mutateAsync: vi.fn().mockResolvedValue({}),
		isPending: false,
	};
	state.createCollection = {
		mutateAsync: vi.fn().mockResolvedValue({ id: "new-col" }),
		isPending: false,
	};
	state.updateCollection = {
		mutateAsync: vi.fn().mockResolvedValue({ id: "col-1" }),
		isPending: false,
	};
	state.deleteCollection = {
		mutateAsync: vi.fn().mockResolvedValue({}),
		isPending: false,
	};
	state.invalidateQueries = vi.fn();
	state.conversations = [
		{
			id: "chat-previous",
			title: "Open ticket",
			agent_name: "Triage Agent",
		},
	];
	state.createConversation = {
		mutateAsync: vi.fn().mockResolvedValue({ id: "chat-new" }),
	};
});

describe("Home", () => {
	it("filters by search, resource type, organization, and selected collection", async () => {
		state.isPlatformAdmin = true;
		const { user } = renderHome(["/?collection=col-1"]);

		expect(
			screen.getByRole("heading", { name: "Operations" }),
		).toBeInTheDocument();
		expect(screen.getByText("Daily customer work")).toBeInTheDocument();
		expect(screen.getByText("2 resources")).toBeInTheDocument();

		await user.click(
			screen.getByRole("combobox", { name: "Resource type" }),
		);
		await user.click(screen.getByRole("option", { name: /^Forms/ }));
		expect(screen.getByTestId("location-probe")).toHaveTextContent(
			"type=form",
		);
		const browse = within(
			screen.getByRole("region", { name: "Browse resources" }),
		);
		expect(
			browse.getByText("Intake Form").closest("button"),
		).toBeInTheDocument();
		expect(browse.queryByText("Dispatch Board")).not.toBeInTheDocument();

		await user.type(
			screen.getByLabelText("Search Home resources"),
			"dispatch",
		);
		expect(screen.getByTestId("location-probe")).toHaveTextContent(
			"q=dispatch",
		);
		expect(
			screen.getByText(/No resources match these filters/),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Clear collection filter" }),
		);
		await waitFor(() =>
			expect(screen.getByTestId("location-probe")).not.toHaveTextContent(
				"collection=col-1",
			),
		);
	});

	it("clears the selected collection when organization scope changes", async () => {
		state.isPlatformAdmin = true;
		const { user } = renderHome(["/?collection=col-1"]);

		expect(screen.getByTestId("location-probe")).toHaveTextContent(
			"collection=col-1",
		);

		await user.click(
			screen.getByRole("combobox", { name: "Organization filter" }),
		);
		await user.click(screen.getByRole("option", { name: "Acme" }));

		await waitFor(() =>
			expect(screen.getByTestId("location-probe")).toHaveTextContent(
				"org=org-1",
			),
		);
		expect(screen.getByTestId("location-probe")).not.toHaveTextContent(
			"collection=col-1",
		);
	});

	it("pins resources through the preference mutation", async () => {
		const { user } = renderHome();

		await user.click(
			screen.getAllByRole("button", { name: "Pin Intake Form" })[0],
		);

		expect(state.preference.mutateAsync).toHaveBeenCalledWith({
			params: { path: { resource_key: "form:intake" } },
			body: { pinned: true, opened: false },
		});
	});

	it("opens apps/forms directly and records recent work", async () => {
		const { user } = renderHome();

		await user.click(
			screen.getAllByRole("button", {
				name: "Intake Form",
			})[0],
		);

		await waitFor(() =>
			expect(screen.getByTestId("location-probe")).toHaveTextContent(
				"/forms/form-1/start",
			),
		);
		expect(state.preference.mutateAsync).toHaveBeenCalledWith({
			params: { path: { resource_key: "form:intake" } },
			body: { opened: true },
		});
	});

	it("starts an agent conversation before navigating", async () => {
		const { user } = renderHome();

		await user.click(
			screen.getAllByRole("button", {
				name: "Triage Agent",
			})[0],
		);

		await waitFor(() =>
			expect(state.createConversation.mutateAsync).toHaveBeenCalledWith({
				body: { agent_id: "agent-1", channel: "chat" },
			}),
		);
		expect(state.preference.mutateAsync).toHaveBeenCalledWith({
			params: { path: { resource_key: "agent:triage" } },
			body: { opened: true },
		});
		await waitFor(() =>
			expect(screen.getByTestId("location-probe")).toHaveTextContent(
				"/chat/chat-new",
			),
		);
	});

	it("creates and selects a collection from Home", async () => {
		const { user } = renderHome();

		await user.click(
			screen.getByRole("button", { name: "New collection" }),
		);
		await user.type(screen.getByLabelText("Name"), "Launch kit");
		await user.click(
			screen.getByRole("checkbox", { name: /Dispatch Board/ }),
		);
		await user.click(
			screen.getByRole("button", { name: "Save collection" }),
		);

		await waitFor(() =>
			expect(state.createCollection.mutateAsync).toHaveBeenCalled(),
		);
		expect(state.createCollection.mutateAsync).toHaveBeenCalledWith({
			body: expect.objectContaining({
				name: "Launch kit",
				resource_keys: ["app:dispatch"],
			}),
		});
		expect(state.invalidateQueries).toHaveBeenCalledWith({
			queryKey: ["get", "/api/home"],
		});
		await waitFor(() =>
			expect(screen.getByTestId("location-probe")).toHaveTextContent(
				"collection=new-col",
			),
		);
	});

	it("updates and removes the selected collection", async () => {
		const { user } = renderHome(["/?collection=col-1"]);

		await user.click(
			screen.getByRole("button", { name: "Edit Operations" }),
		);
		await user.clear(screen.getByLabelText("Description"));
		await user.type(
			screen.getByLabelText("Description"),
			"Updated shortcuts",
		);
		await user.click(
			screen.getByRole("button", { name: "Save collection" }),
		);
		await waitFor(() =>
			expect(state.updateCollection.mutateAsync).toHaveBeenCalledWith({
				params: { path: { collection_id: "col-1" } },
				body: expect.objectContaining({
					description: "Updated shortcuts",
				}),
			}),
		);

		await user.click(
			screen.getByRole("button", { name: "Edit Operations" }),
		);
		await user.click(screen.getByRole("button", { name: "Delete" }));
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Delete this collection?",
		);
		await user.click(
			screen.getByRole("button", { name: "Confirm delete" }),
		);
		await waitFor(() =>
			expect(state.deleteCollection.mutateAsync).toHaveBeenCalledWith({
				params: { path: { collection_id: "col-1" } },
			}),
		);
		await waitFor(() =>
			expect(screen.getByTestId("location-probe")).not.toHaveTextContent(
				"collection=col-1",
			),
		);
	});

	it("surfaces Home loading and recoverable load failure", async () => {
		Object.assign(state.home, {
			data: undefined,
			isLoading: true,
			error: null,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
		const loading = renderHome();
		expect(screen.getByText("Loading your workspace…")).toBeInTheDocument();
		loading.unmount();

		const refetch = vi.fn();
		Object.assign(state.home, {
			data: undefined,
			isLoading: false,
			error: new Error("nope"),
			isError: true,
			isFetching: false,
			refetch,
		});
		const { user } = renderHome();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"could not be retrieved",
		);
		await user.click(screen.getByRole("button", { name: "Try again" }));
		expect(refetch).toHaveBeenCalledOnce();
	});
});

it("uses a plain Workspace title for users and title navigation for admins", () => {
	const ordinary = renderHome();
	expect(
		screen.getByRole("heading", { name: "Workspace", level: 1 }),
	).toBeInTheDocument();
	expect(
		screen.queryByRole("navigation", { name: "Workspace views" }),
	).not.toBeInTheDocument();
	ordinary.unmount();

	state.isPlatformAdmin = true;
	renderHome();
	const navigation = screen.getByRole("navigation", {
		name: "Workspace views",
	});
	const workspace = within(navigation).getByRole("link", {
		name: "Workspace",
	});
	expect(workspace).toHaveAttribute("aria-current", "page");
	expect(workspace).toContainElement(
		screen.getByRole("heading", { name: "Workspace", level: 1 }),
	);
	expect(within(navigation).getAllByRole("link")).toHaveLength(2);
	expect(
		within(navigation).getByRole("link", { name: "Dashboard" }),
	).toBeInTheDocument();
});

it("puts filtered catalog results ahead of shortcuts and keeps Dashboard admin-only", async () => {
	const { user } = renderHome();
	expect(
		screen.queryByRole("link", { name: "Dashboard" }),
	).not.toBeInTheDocument();
	await user.click(screen.getByRole("combobox", { name: "Resource type" }));
	await user.click(screen.getByRole("option", { name: /^Agents/ }));
	expect(
		screen.queryByRole("region", { name: "Pinned resources" }),
	).not.toBeInTheDocument();
	expect(
		screen.getByRole("button", { name: "Triage Agent" }),
	).toBeInTheDocument();
	await user.click(screen.getByRole("combobox", { name: "Resource type" }));
	await user.click(screen.getByRole("option", { name: /^All types/ }));
	expect(
		screen.getByRole("region", { name: "Browse resources" }),
	).toBeInTheDocument();
});
it("opens and dismisses collection creation from sidebar navigation", async () => {
	const { user } = renderHome(["/?newCollection=1"]);
	expect(
		screen.getByRole("dialog", { name: "New collection" }),
	).toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Cancel" }));
	expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	expect(screen.getByTestId("location-probe")).not.toHaveTextContent(
		"newCollection",
	);
});

it("switches catalog presentation and sorts recent launches", async () => {
	const { user } = renderHome(["/?type=all&q= "]);
	await user.click(screen.getByRole("combobox", { name: "Sort resources" }));
	await user.click(screen.getByRole("option", { name: "Recently opened" }));
	expect(screen.getByTestId("location-probe")).toHaveTextContent(
		"sort=recent",
	);
	await user.click(screen.getByRole("button", { name: "List view" }));
	expect(screen.getByRole("button", { name: "List view" })).toHaveAttribute(
		"aria-pressed",
		"true",
	);
	expect(screen.getByTestId("location-probe")).toHaveTextContent("view=list");
});

it("shows one catalog with pinned items first and collection navigation", () => {
	state.home.data = { resources: [...resources].reverse(), collections };
	renderHome();
	const cards = screen.getAllByRole("article");
	expect(cards).toHaveLength(3);
	expect(
		within(cards[0]).getByRole("button", { name: "Dispatch Board" }),
	).toBeVisible();
	expect(
		screen.getAllByRole("button", { name: "Dispatch Board" }),
	).toHaveLength(1);
	expect(
		screen.getByRole("navigation", { name: "Collections" }),
	).toBeVisible();
	expect(
		screen.queryByRole("region", { name: "Pinned resources" }),
	).not.toBeInTheDocument();
});

it("prioritizes matching titles over pinned description matches in search", () => {
	state.home.data = {
		resources: resources.map((r) =>
			r.pinned ? { ...r, description: "Intake integration" } : r,
		),
		collections,
	};
	renderHome(["/?q=intake"]);
	expect(
		within(screen.getAllByRole("article")[0]).getByRole("button", {
			name: "Intake Form",
		}),
	).toBeVisible();
});
