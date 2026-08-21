import { describe, expect, it } from "vitest";
import { useState } from "react";

import {
	renderWithProviders,
	screen,
	waitFor,
} from "@/test-utils";

import {
	RoleAssignmentEditor,
	areBoundariesEqual,
	addRoleAssignment,
	boundaryKey,
	createDefaultBoundaries,
	dedupeBoundaries,
	isPlatformLockedRole,
	removeRoleAssignment,
	summarizeBoundaries,
	toggleBoundarySelection,
	updateRoleAssignmentBoundaries,
	validateBoundaries,
	type RoleAssignmentDraft,
	type RoleAssignmentEditorOrganization,
	type RoleAssignmentEditorOrganizationGroup,
	type RoleAssignmentEditorRole,
	type RoleBoundaryDraft,
} from "./RoleAssignmentEditor";

const roles: RoleAssignmentEditorRole[] = [
	{ id: "role-viewer", name: "Viewer", description: "Read-only role" },
	{
		id: "role-platform",
		name: "Platform Admin",
		key: "platform.admin",
		is_builtin: true,
	},
	{ id: "role-builder", name: "Builder" },
];

const organizations: RoleAssignmentEditorOrganization[] = [
	{ id: "org-1", name: "Acme Corp" },
	{ id: "org-2", name: "Bravo Labs" },
];

const organizationGroups: RoleAssignmentEditorOrganizationGroup[] = [
	{ id: "grp-1", name: "North Team" },
];

function Harness({
	initialValue,
	defaultBoundary,
	platformAdminRoleIds = ["role-platform"],
	onChange,
}: {
	initialValue: RoleAssignmentDraft[];
	defaultBoundary?: RoleBoundaryDraft | null;
	platformAdminRoleIds?: string[];
	onChange?: (next: RoleAssignmentDraft[]) => void;
}) {
	const [value, setValue] = useState(initialValue);

	return (
		<RoleAssignmentEditor
			roles={roles}
			value={value}
			organizations={organizations}
			organizationGroups={organizationGroups}
			defaultBoundary={defaultBoundary ?? null}
			platformAdminRoleIds={platformAdminRoleIds}
			onChange={(next) => {
				setValue(next);
				onChange?.(next);
			}}
		/>
	);
}

describe("RoleAssignmentEditor helpers", () => {
	it("summarizes and compares every boundary kind", () => {
		const boundaries: RoleBoundaryDraft[] = [
			{ boundary_kind: "platform" },
			{ boundary_kind: "managed_organizations" },
			{ boundary_kind: "organization", organization_id: "org-1" },
			{
				boundary_kind: "organization_group",
				organization_group_id: "grp-1",
			},
		];

		expect(boundaryKey(boundaries[2]!)).toBe("organization:org-1:");
		expect(
			areBoundariesEqual(boundaries[2]!, {
				boundary_kind: "organization",
				organization_id: "org-1",
			}),
		).toBe(true);
		expect(
			summarizeBoundaries(boundaries, {
				organizations: new Map([["org-1", "Acme Corp"]]),
				organizationGroups: new Map([["grp-1", "North Team"]]),
			}),
		).toBe("Platform · Managed organizations · Acme Corp · North Team");
	});

	it("dedupes and validates boundary selections", () => {
		const duplicateBoundary: RoleBoundaryDraft = {
			boundary_kind: "organization",
			organization_id: "org-1",
		};

		expect(
			dedupeBoundaries([duplicateBoundary, duplicateBoundary]).length,
		).toBe(1);
		expect(validateBoundaries([])).toBe("At least one boundary is required.");
		expect(
			validateBoundaries([duplicateBoundary, duplicateBoundary]),
		).toBe("Duplicate boundary selections are not allowed.");
		expect(
			validateBoundaries(
				[
					{ boundary_kind: "platform" },
					duplicateBoundary,
				],
				true,
			),
		).toBe("Platform Admin is fixed to Platform only.");
	});

	it("supports add, remove, update, and toggle helpers", () => {
		const initial: RoleAssignmentDraft[] = [];
		expect(
			addRoleAssignment(
				initial,
				"role-viewer",
				{ boundary_kind: "organization", organization_id: "org-1" },
				false,
			),
		).toEqual([
			{
				role_id: "role-viewer",
				boundaries: [
					{ boundary_kind: "organization", organization_id: "org-1" },
				],
			},
		]);
		expect(
			createDefaultBoundaries(
				{ boundary_kind: "platform" },
				true,
			),
		).toEqual([{ boundary_kind: "platform" }]);
		expect(
			removeRoleAssignment(
				[
					{
						role_id: "role-viewer",
						boundaries: [{ boundary_kind: "platform" }],
					},
				],
				"role-viewer",
			),
		).toEqual([]);
		expect(
			updateRoleAssignmentBoundaries(
				[
					{
						role_id: "role-viewer",
						boundaries: [{ boundary_kind: "platform" }],
					},
				],
				"role-viewer",
				[
					{
						boundary_kind: "organization",
						organization_id: "org-1",
					},
					{
						boundary_kind: "organization",
						organization_id: "org-1",
					},
				],
				false,
			),
		).toEqual([
			{
				role_id: "role-viewer",
				boundaries: [
					{ boundary_kind: "organization", organization_id: "org-1" },
				],
			},
		]);
		expect(
			toggleBoundarySelection([], {
				boundary_kind: "managed_organizations",
			}),
		).toEqual([{ boundary_kind: "managed_organizations" }]);
		expect(
			isPlatformLockedRole({ id: "role-platform", name: "Platform Admin" }, [
				"role-platform",
			]),
		).toBe(true);
	});
});

describe("RoleAssignmentEditor", () => {
	it("adds a role with an explicit default boundary", async () => {
		const { user } = renderWithProviders(
			<Harness
				initialValue={[]}
				defaultBoundary={{ boundary_kind: "organization", organization_id: "org-1" }}
			/>,
		);

		await user.click(screen.getByRole("combobox", { name: /add role/i }));
		await user.click(screen.getByText("Viewer"));

		await waitFor(() =>
			expect(screen.getByText("Viewer")).toBeInTheDocument(),
		);
		expect(
			screen.getByText("Acme Corp", {
				selector: "p.text-sm.text-muted-foreground",
			}),
		).toBeInTheDocument();
		expect(screen.getByText(/custom access/i)).toBeInTheDocument();
	});

	it("updates boundaries through the access editor", async () => {
		const { user } = renderWithProviders(
			<Harness
				initialValue={[
					{
						role_id: "role-builder",
						boundaries: [
							{ boundary_kind: "organization", organization_id: "org-1" },
						],
					},
				]}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /edit access for builder/i }),
		);
		await user.click(screen.getByText("Bravo Labs"));
		await user.click(screen.getByText("North Team"));
		await user.click(screen.getByText("Managed organizations"));

		expect(
			screen.getByText(
				"Acme Corp · Bravo Labs · North Team · Managed organizations",
				{ selector: "p.text-sm.text-muted-foreground" },
			),
		).toBeInTheDocument();
	});

	it("keeps platform admin fixed to platform only", async () => {
		const { user } = renderWithProviders(
			<Harness
				initialValue={[
					{
						role_id: "role-platform",
						boundaries: [{ boundary_kind: "platform" }],
					},
				]}
			/>,
		);

		expect(screen.getByText(/platform only/i)).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: /edit access for platform admin/i }),
		);

		expect(
			screen.getByText("Platform", {
				selector: "p.text-sm.text-muted-foreground",
			}),
		).toBeInTheDocument();
		expect(screen.getByText("Organizations")).toBeInTheDocument();
	});

	it("shows a validation message when an assignment has no boundaries", () => {
		renderWithProviders(
			<Harness
				initialValue={[
					{
						role_id: "role-viewer",
						boundaries: [],
					},
				]}
			/>,
		);

		expect(
			screen.getByText(/at least one boundary is required/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("alert"),
		).toBeInTheDocument();
	});
});
