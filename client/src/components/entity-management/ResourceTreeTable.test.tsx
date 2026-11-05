import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ResourceTreeTable } from "./ResourceTreeTable";
import type { EntityWithScope, Organization, Role } from "./types";

const dependencyGraph = vi.fn();
const { mockUseAuthorization } = vi.hoisted(() => ({
	mockUseAuthorization: vi.fn(),
}));

vi.mock("@/hooks/useDependencyGraph", () => ({
	useDependencyGraph: (entityType?: string, entityId?: string) =>
		dependencyGraph(entityType, entityId),
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockUseAuthorization(),
}));

mockUseAuthorization.mockReturnValue({ authorization: undefined });

const entities = [
	entity("app-1", "Covi Portal", "app", "org-1", "role_based"),
	entity(
		"workflow-1",
		"Create service request",
		"workflow",
		null,
		"authenticated",
	),
	entity("form-1", "Service request form", "form", "org-1", "authenticated"),
	entity("agent-1", "Support assistant", "agent", "org-1", "role_based"),
];

const organizations = [{ id: "org-1", name: "Northwind" }] as Organization[];
const roles = [{ id: "role-1", name: "Service Desk" }] as Role[];

it("renders compact resource rows with type, scope, and access metadata", () => {
	dependencyGraph.mockReturnValue({
		data: null,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	renderTable({ entities: entities.slice(0, 1), allEntities: entities });

	expect(screen.queryByRole("columnheader")).not.toBeInTheDocument();
	const items = resourceItems();
	expect(items).toHaveLength(1);
	expect(items[0]).toHaveTextContent("Covi Portal");
	expect(items[0]).toHaveTextContent("App");
	expect(items[0]).toHaveTextContent("Northwind");
	expect(items[0]).toHaveTextContent("Service Desk");
});

it("loads relationships from the expanded row and preserves the root list", async () => {
	const user = userEvent.setup();
	const onVisibleKeysChange = vi.fn();
	const onConnectedKeysChange = vi.fn();
	dependencyGraph.mockReturnValue({
		data: {
			root_id: "app:app-1",
			nodes: [],
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
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});

	renderTable({
		entities: entities.slice(0, 1),
		allEntities: entities,
		onVisibleKeysChange,
		onConnectedKeysChange,
	});
	expect(onVisibleKeysChange).toHaveBeenLastCalledWith(["app:app-1"]);
	expect(onConnectedKeysChange).toHaveBeenLastCalledWith([]);
	await user.click(
		screen.getAllByRole("button", { name: "Expand Covi Portal" })[0],
	);

	expect(dependencyGraph).toHaveBeenLastCalledWith("app", "app-1");
	const items = resourceItems();
	expect(items).toHaveLength(3);
	expect(items[0]).toHaveTextContent("Covi Portal");
	expect(items[1]).toHaveTextContent("Create service request");
	expect(items[1]).toHaveTextContent("Used by Covi Portal");
	expect(items[2]).toHaveTextContent("Service request form");
	expect(items[2]).toHaveTextContent("Uses Create service request");
	expect(onVisibleKeysChange).toHaveBeenLastCalledWith([
		"app:app-1",
		"workflow:workflow-1",
		"form:form-1",
	]);
	expect(onConnectedKeysChange).toHaveBeenLastCalledWith([
		"app:app-1",
		"workflow:workflow-1",
		"form:form-1",
	]);
	await user.click(
		screen.getAllByRole("button", { name: "Collapse Covi Portal" })[0],
	);
	expect(resourceItems()).toHaveLength(1);
	expect(onVisibleKeysChange).toHaveBeenLastCalledWith(["app:app-1"]);
	expect(onConnectedKeysChange).toHaveBeenLastCalledWith([]);
});

it("reports only the expanded graph for connected selection", async () => {
	const user = userEvent.setup();
	const onConnectedKeysChange = vi.fn();
	dependencyGraph.mockReturnValue({
		data: {
			root_id: "app:app-1",
			nodes: [],
			edges: [
				{
					source: "app:app-1",
					target: "workflow:workflow-1",
					relationship: "uses",
				},
			],
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});

	renderTable({
		entities: [entities[0], entities[3]],
		allEntities: entities,
		onConnectedKeysChange,
	});
	await user.click(
		screen.getAllByRole("button", { name: "Expand Covi Portal" })[0],
	);

	expect(onConnectedKeysChange).toHaveBeenLastCalledWith([
		"app:app-1",
		"workflow:workflow-1",
	]);
	expect(onConnectedKeysChange).not.toHaveBeenCalledWith(
		expect.arrayContaining(["agent:agent-1"]),
	);
});

it("selects only the clicked parent row and leaves related rows untouched", async () => {
	const user = userEvent.setup();
	const onSelect = vi.fn();
	dependencyGraph.mockReturnValue({
		data: {
			root_id: "app:app-1",
			nodes: [],
			edges: [
				{
					source: "app:app-1",
					target: "workflow:workflow-1",
					relationship: "uses",
				},
			],
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});

	renderTable({
		entities: [entities[0], entities[1]],
		allEntities: entities,
		onSelect,
	});
	await user.click(
		screen.getAllByRole("button", { name: "Expand Covi Portal" })[0],
	);

	const items = resourceItems();
	await user.click(within(items[0]).getByRole("checkbox"));

	expect(onSelect).toHaveBeenCalledWith("app:app-1", true);
	expect(within(items[1]).getByRole("checkbox")).not.toBeChecked();
});

it("shares selected state when the same resource appears as root and related", async () => {
	const user = userEvent.setup();
	dependencyGraph.mockReturnValue({
		data: {
			root_id: "app:app-1",
			nodes: [],
			edges: [
				{
					source: "app:app-1",
					target: "workflow:workflow-1",
					relationship: "uses",
				},
			],
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});

	renderTable({
		entities: [entities[0], entities[1]],
		allEntities: entities,
		selectedIds: new Set(["workflow:workflow-1"]),
	});
	await user.click(
		screen.getAllByRole("button", { name: "Expand Covi Portal" })[0],
	);

	const workflowRows = resourceItems().filter((item) =>
		item.textContent?.includes("Create service request"),
	);
	expect(workflowRows).toHaveLength(2);
	expect(within(workflowRows[0]).getByRole("checkbox")).toBeChecked();
	expect(within(workflowRows[1]).getByRole("checkbox")).toBeChecked();
});

it("keeps composite selection distinct and routes delete actions with actual ids", async () => {
	const user = userEvent.setup();
	const onSelect = vi.fn();
	const onDelete = vi.fn();
	dependencyGraph.mockReturnValue({
		data: null,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	renderTable({
		entities: [
			entity("shared", "Portal", "app", null, "authenticated"),
			entity(
				"shared",
				"Provision customer",
				"workflow",
				null,
				"authenticated",
			),
		],
		allEntities: [
			entity("shared", "Portal", "app", null, "authenticated"),
			entity(
				"shared",
				"Provision customer",
				"workflow",
				null,
				"authenticated",
			),
		],
		selectedIds: new Set(["workflow:shared"]),
		onSelect,
		onDelete,
	});
	const items = resourceItems();
	expect(within(items[0]).getByRole("checkbox")).not.toBeChecked();
	expect(within(items[1]).getByRole("checkbox")).toBeChecked();
	await user.click(within(items[0]).getByRole("checkbox"));
	expect(onSelect).toHaveBeenCalledWith("app:shared", true);
	await user.click(
		within(items[0]).getByRole("button", {
			name: "More actions for Portal",
		}),
	);
	await user.click(screen.getByRole("menuitem", { name: "Delete app" }));
	expect(onDelete).toHaveBeenCalledWith("shared", "Portal", "app");
});

it("shows loading and error states inline on the expanded row", async () => {
	const user = userEvent.setup();
	const refetch = vi.fn();
	dependencyGraph.mockReturnValue({
		data: null,
		isLoading: true,
		isError: false,
		isFetching: false,
		refetch,
	});
	const { rerender } = renderTable({
		entities: entities.slice(0, 1),
		allEntities: entities,
	});
	await user.click(
		screen.getAllByRole("button", { name: "Expand Covi Portal" })[0],
	);
	expect(
		screen.getAllByRole("button", { name: "Collapse Covi Portal" })[0],
	).toHaveAttribute("aria-expanded", "true");
	dependencyGraph.mockReturnValue({
		data: null,
		isLoading: false,
		isError: true,
		isFetching: false,
		refetch,
	});
	rerender(table({ entities: entities.slice(0, 1), allEntities: entities }));
	expect(resourceItems()[0]).toHaveTextContent(
		"Could not load related resources",
	);
	await user.click(screen.getAllByRole("button", { name: "Retry" })[0]);
	expect(refetch).toHaveBeenCalled();
});

it("shows an inline empty relationships state when expansion has no children", async () => {
	const user = userEvent.setup();
	dependencyGraph.mockReturnValue({
		data: {
			root_id: "app:app-1",
			nodes: [],
			edges: [],
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	renderTable({ entities: entities.slice(0, 1), allEntities: entities });
	await user.click(
		screen.getAllByRole("button", { name: "Expand Covi Portal" })[0],
	);
	expect(resourceItems()[0]).toHaveTextContent("No related resources");
});

it("renders expansion controls from relationship availability instead of directional counts", () => {
	dependencyGraph.mockReturnValue({
		data: null,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	renderTable({
		entities: [
			entity(
				"form-outgoing",
				"Workflow launcher form",
				"form",
				null,
				"authenticated",
				0,
				true,
			),
			entity(
				"workflow-inbound-only",
				"Inbound workflow",
				"workflow",
				null,
				"authenticated",
				0,
				true,
			),
			entity(
				"app-empty",
				"Zero linked app",
				"app",
				null,
				"authenticated",
				null,
				false,
			),
		],
		allEntities: [
			entity(
				"form-outgoing",
				"Workflow launcher form",
				"form",
				null,
				"authenticated",
				0,
				true,
			),
			entity(
				"workflow-inbound-only",
				"Inbound workflow",
				"workflow",
				null,
				"authenticated",
				0,
				true,
			),
			entity(
				"app-empty",
				"Zero linked app",
				"app",
				null,
				"authenticated",
				null,
				false,
			),
		],
	});

	expect(
		screen.getAllByRole("button", { name: "Expand Workflow launcher form" })
			.length,
	).toBeGreaterThan(0);
	expect(
		screen.getAllByRole("button", { name: "Expand Inbound workflow" })
			.length,
	).toBeGreaterThan(0);
	expect(
		screen.queryByRole("button", { name: "Expand Zero linked app" }),
	).not.toBeInTheDocument();
});

function renderTable(overrides: Partial<TableProps> = {}) {
	return render(table(overrides));
}

function resourceItems() {
	return within(screen.getByRole("list", { name: "Resources" })).getAllByRole(
		"listitem",
	);
}

function table(overrides: Partial<TableProps> = {}) {
	const props: TableProps = {
		entities,
		allEntities: entities,
		selectedIds: new Set(),
		onSelect: vi.fn(),
		onDelete: vi.fn(),
		onVisibleKeysChange: vi.fn(),
		onConnectedKeysChange: vi.fn(),
		...overrides,
	};
	return (
		<ResourceTreeTable
			entities={props.entities}
			allEntities={props.allEntities}
			organizations={organizations}
			roles={roles}
			selectedIds={props.selectedIds}
			allSelected={false}
			someSelected={props.selectedIds.size > 0}
			onSelectAll={vi.fn()}
			onSelect={props.onSelect}
			onVisibleKeysChange={props.onVisibleKeysChange}
			onConnectedKeysChange={props.onConnectedKeysChange}
			onDelete={props.onDelete}
		/>
	);
}

interface TableProps {
	entities: EntityWithScope[];
	allEntities: EntityWithScope[];
	selectedIds: Set<string>;
	onSelect: (entityKey: string, selected: boolean) => void;
	onVisibleKeysChange: (entityKeys: string[]) => void;
	onConnectedKeysChange: (entityKeys: string[]) => void;
	onDelete: (
		entityId: string,
		entityName: string,
		entityType: EntityWithScope["entityType"],
	) => void;
}

function entity(
	id: string,
	name: string,
	entityType: EntityWithScope["entityType"],
	organizationId: string | null,
	accessLevel: string | null,
	usedByCount = entityType === "app" ? null : 1,
	hasRelationships = usedByCount === null ? true : usedByCount > 0,
): EntityWithScope {
	return {
		key: `${entityType}:${id}`,
		id,
		name,
		entityType,
		organizationId,
		accessLevel,
		roleIds: entityType === "app" ? ["role-1"] : [],
		createdAt: "2026-01-01T00:00:00Z",
		usedByCount,
		hasRelationships,
		original: {},
	} as EntityWithScope;
}
