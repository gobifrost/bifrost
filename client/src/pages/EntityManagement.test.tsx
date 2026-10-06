import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import type { ComponentProps, ReactNode } from "react";

import { EntityManagement } from "./EntityManagement";

const authFetch = vi.hoisted(() => vi.fn());
const updateWorkflow = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const sourceState = vi.hoisted(() => ({
	formsLoading: false,
	workflowAccess: "authenticated",
	reset() {
		this.formsLoading = false;
		this.workflowAccess = "authenticated";
	},
}));
const mediaState = vi.hoisted(() => ({
	desktop: true,
	reset() {
		this.desktop = true;
	},
}));

vi.mock("framer-motion", () => {
	return {
		AnimatePresence: ({ children }: { children: ReactNode }) => (
			<>{children}</>
		),
		motion: {
			aside: ({
				children,
				initial: _initial,
				animate: _animate,
				exit: _exit,
				transition: _transition,
				...props
			}: ComponentProps<"aside"> & {
				initial?: unknown;
				animate?: unknown;
				exit?: unknown;
				transition?: unknown;
			}) => <aside {...props}>{children}</aside>,
		},
		useReducedMotion: () => false,
	};
});

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetch(...args),
	apiClient: { POST: (...args: unknown[]) => apiPost(...args) },
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useIsDesktop: () => mediaState.desktop,
	useMediaQuery: () => mediaState.desktop,
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflows: () => ({
		data: [
			{
				id: "workflow-1",
				name: "Create service request",
				organization_id: null,
				access_level: sourceState.workflowAccess,
				created_at: "2026-01-01T00:00:00Z",
				used_by_count: 1,
				is_solution_managed: false,
				solution_id: null,
			},
		],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useUpdateWorkflow: () => ({ mutateAsync: updateWorkflow }),
}));

vi.mock("@/hooks/useForms", () => ({
	useForms: () => ({
		data: [
			{
				id: "form-1",
				name: "Service request intake",
				organization_id: null,
				access_level: "authenticated",
				created_at: "2026-01-01T00:00:00Z",
				dependency_count: 1,
				is_active: true,
				is_solution_managed: false,
				solution_id: null,
			},
		],
		isLoading: sourceState.formsLoading,
		isError: false,
		isFetching: sourceState.formsLoading,
		refetch: vi.fn(),
	}),
	useUpdateForm: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("@/hooks/useAgents", () => ({
	useAgents: () => ({
		data: [],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useUpdateAgent: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("@/hooks/useApplications", () => ({
	useApplications: () => ({
		data: {
			applications: [
				{
					id: "app-1",
					name: "Covi Portal",
					slug: "covi-portal",
					organization_id: null,
					access_level: "authenticated",
					role_ids: [],
					created_at: "2026-01-01T00:00:00Z",
					is_solution_managed: false,
					solution_id: null,
				},
				{
					id: "app-2",
					name: "Unrelated Portal",
					slug: "unrelated-portal",
					organization_id: null,
					access_level: "authenticated",
					role_ids: [],
					created_at: "2026-01-01T00:00:00Z",
					is_solution_managed: false,
					solution_id: null,
				},
			],
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useUpdateApplication: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => ({
		data: [],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/hooks/useDependencyGraph", () => ({
	useDependencyGraph: (entityType?: string, entityId?: string) => ({
		data:
			entityType === "app" && entityId === "app-1"
				? {
						root_id: "app:app-1",
						nodes: [
							{
								id: "app:app-1",
								type: "app",
								name: "Covi Portal",
								org_id: null,
							},
							{
								id: "workflow:workflow-1",
								type: "workflow",
								name: "Create service request",
								org_id: null,
							},
							{
								id: "form:form-1",
								type: "form",
								name: "Service request intake",
								org_id: null,
							},
						],
						edges: [
							{
								source: "app:app-1",
								target: "workflow:workflow-1",
								relationship: "uses",
							},
							{
								source: "form:form-1",
								target: "workflow:workflow-1",
								relationship: "uses",
							},
						],
					}
				: undefined,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/hooks/useAssignEntityRole", () => ({
	useAssignEntityRole: () => vi.fn(),
}));

beforeEach(() => {
	authFetch.mockReset();
	updateWorkflow.mockReset();
	updateWorkflow.mockResolvedValue({});
	apiPost.mockReset();
	sourceState.reset();
	mediaState.reset();
});

function relationshipAvailability(overrides: Record<string, boolean> = {}) {
	return {
		data: {
			has_relationships: {
				"app:app-1": true,
				"app:app-2": false,
				"workflow:workflow-1": true,
				"form:form-1": true,
				...overrides,
			},
		},
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

it("expands related resources inline from a search result", async () => {
	const user = userEvent.setup();
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	apiPost.mockResolvedValue(relationshipAvailability());
	render(
		<QueryClientProvider client={queryClient}>
			<EntityManagement />
		</QueryClientProvider>,
	);

	await user.type(
		screen.getByRole("textbox", { name: "Search entities" }),
		"Covi",
	);
	expect(screen.getAllByText("Covi Portal").length).toBeGreaterThan(0);
	expect(
		screen.queryByText("Create service request"),
	).not.toBeInTheDocument();

	const expandButtons = await screen.findAllByRole("button", {
		name: "Expand Covi Portal",
	});
	await user.click(expandButtons[0]);

	expect(
		screen.getByRole("textbox", { name: "Search entities" }),
	).toHaveValue("Covi");
	expect(
		screen.getAllByText("Create service request").length,
	).toBeGreaterThan(0);
	expect(
		screen.getAllByText("Service request intake").length,
	).toBeGreaterThan(0);

	await user.click(
		screen.getAllByRole("checkbox", {
			name: "Select all visible entities",
		})[0],
	);
	expect(screen.getByText("3 selected")).toBeInTheDocument();
	await user.clear(screen.getByRole("textbox", { name: "Search entities" }));
	await user.type(
		screen.getByRole("textbox", { name: "Search entities" }),
		"No matching resource",
	);
	expect(screen.getByRole("status")).toHaveTextContent(
		"3 selected (3 outside this view)",
	);
});

it("selects only the expanded connected graph from the directory action", async () => {
	const user = userEvent.setup();
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	apiPost.mockResolvedValue(relationshipAvailability());
	render(
		<QueryClientProvider client={queryClient}>
			<EntityManagement />
		</QueryClientProvider>,
	);

	await screen.findByText("Unrelated Portal");
	const selectConnected = screen.getByRole("button", {
		name: "Select connected",
	});
	expect(selectConnected).toBeDisabled();

	await user.click(
		screen.getAllByRole("button", { name: "Expand Covi Portal" })[0],
	);
	expect(selectConnected).toBeEnabled();
	await user.click(selectConnected);

	expect(screen.getByRole("status")).toHaveTextContent("3 selected");
	expect(
		screen.getByRole("checkbox", { name: "Select Unrelated Portal" }),
	).not.toBeChecked();
});

it("unmounts the desktop bulk editor when closed and restores footer focus", async () => {
	const user = userEvent.setup();
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	apiPost.mockResolvedValue(relationshipAvailability());
	render(
		<QueryClientProvider client={queryClient}>
			<EntityManagement />
		</QueryClientProvider>,
	);

	await screen.findByText("Covi Portal");
	await user.click(
		screen.getByRole("checkbox", { name: "Select Covi Portal" }),
	);
	const edit = screen.getByRole("button", { name: "Edit selected" });
	await user.click(edit);
	expect(
		screen.getByRole("dialog", { name: "Edit 1 resource" }),
	).toBeVisible();

	await user.click(screen.getByRole("button", { name: "Close" }));
	expect(
		screen.queryByRole("dialog", { name: "Edit 1 resource" }),
	).not.toBeInTheDocument();
	expect(edit).toHaveFocus();
	await user.click(edit);
	await user.click(screen.getByRole("button", { name: "Clear" }));
	await user.click(
		screen.getByRole("checkbox", { name: "Select Covi Portal" }),
	);
	expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("waits for initial relationship availability before rendering resources", () => {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	apiPost.mockReturnValue(new Promise(() => {}));

	render(
		<QueryClientProvider client={queryClient}>
			<EntityManagement />
		</QueryClientProvider>,
	);

	expect(screen.queryByText("Covi Portal")).not.toBeInTheDocument();
	expect(
		screen.queryByRole("button", { name: "Expand Covi Portal" }),
	).not.toBeInTheDocument();
});

it("waits for all initial source queries before requesting availability", () => {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	sourceState.formsLoading = true;

	render(
		<QueryClientProvider client={queryClient}>
			<EntityManagement />
		</QueryClientProvider>,
	);

	expect(
		screen.getByRole("status", { name: "Loading entities…" }),
	).toBeInTheDocument();
	expect(screen.queryByText("Covi Portal")).not.toBeInTheDocument();
	expect(apiPost).not.toHaveBeenCalled();
});

it("keeps rendered resources and chevrons while availability refreshes", async () => {
	const user = userEvent.setup();
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	apiPost.mockResolvedValueOnce(relationshipAvailability());

	render(
		<QueryClientProvider client={queryClient}>
			<EntityManagement />
		</QueryClientProvider>,
	);

	expect((await screen.findAllByText("Covi Portal")).length).toBeGreaterThan(
		0,
	);
	expect(
		screen.getAllByRole("button", { name: "Expand Covi Portal" }).length,
	).toBeGreaterThan(0);

	const refresh = deferred<ReturnType<typeof relationshipAvailability>>();
	apiPost.mockReturnValueOnce(refresh.promise);
	await user.click(screen.getByRole("button", { name: "Refresh" }));

	expect(screen.getAllByText("Covi Portal").length).toBeGreaterThan(0);
	expect(
		screen.getAllByRole("button", { name: "Expand Covi Portal" }).length,
	).toBeGreaterThan(0);

	refresh.resolve(relationshipAvailability());
});

it("filters differences across loaded relationships without including unrelated resources", async () => {
	const user = userEvent.setup();
	sourceState.workflowAccess = "role_based";
	apiPost.mockResolvedValue(relationshipAvailability());
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	render(
		<QueryClientProvider client={queryClient}>
			<EntityManagement />
		</QueryClientProvider>,
	);
	await user.click(
		(
			await screen.findAllByRole("button", { name: "Expand Covi Portal" })
		)[0],
	);
	await user.click(screen.getByRole("button", { name: "Filters" }));
	await user.click(
		screen.getByRole("option", { name: "Related scope/access mismatch" }),
	);
	await user.keyboard("{Escape}");
	expect(screen.queryByText("Unrelated Portal")).not.toBeInTheDocument();
	expect(screen.getAllByText("Covi Portal").length).toBeGreaterThan(0);
	expect(
		screen.getAllByText("Create service request").length,
	).toBeGreaterThan(0);
	expect(
		screen.getAllByText("Service request intake").length,
	).toBeGreaterThan(0);
});

it("clears workflow roles without overriding an explicit access level", async () => {
	const user = userEvent.setup();
	apiPost.mockResolvedValue(relationshipAvailability());
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	render(
		<QueryClientProvider client={queryClient}>
			<EntityManagement />
		</QueryClientProvider>,
	);
	await user.click(
		await screen.findByRole("checkbox", {
			name: "Select Create service request",
		}),
	);
	await user.click(screen.getByRole("button", { name: "Edit selected" }));
	await user.click(
		screen.getByRole("combobox", { name: "Access level change" }),
	);
	await user.click(
		screen.getByRole("option", { name: /^Everyone Any signed-in/ }),
	);
	await user.click(screen.getByRole("combobox", { name: "Roles change" }));
	await user.click(screen.getByRole("option", { name: "Clear roles" }));
	await user.click(screen.getByRole("button", { name: "Apply changes" }));
	expect(updateWorkflow).toHaveBeenCalledExactlyOnceWith("workflow-1", {
		access_level: "everyone",
		role_ids: [],
	});
});
