import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import type { PermissionCatalogEntry, UserAccessMap } from "@/services/access";

const state = vi.hoisted(() => ({
	canReadOrganizations: true,
	accessMapUserId: undefined as string | undefined,
}));

const accessMap: Pick<UserAccessMap, "reach" | "rows"> = {
	reach: [
		{
			kind: "organization",
			organization_id: "org-2",
			organization_name: "Fabrikam",
			label: "Fabrikam",
		},
	],
	rows: [
		{
			place: {
				kind: "organization",
				organization_id: "org-2",
				organization_name: "Fabrikam",
				label: "Fabrikam",
			},
			grants: [
				{
					permission: "tables.read",
					domain: "tables",
					action: "read",
					scope: "per_organization",
					sources: [
						{
							role_id: "role-helpdesk",
							role_name: "Helpdesk",
							via: "additional",
						},
					],
				},
			],
		},
	],
};
const catalog: PermissionCatalogEntry[] = [
	{
		domain: "tables",
		title: "Tables",
		area: "Data & Content",
		description: "",
		who_should_hold: "",
		actions: ["read"],
		names: { "tables.read": "Read Tables" },
		privileged: [],
		scope: "per_organization",
		enforced: true,
	},
];

vi.mock("@/services/access", () => ({
	useUserAccessMap: (userId: string | undefined) => {
		state.accessMapUserId = userId;
		return { data: userId ? accessMap : undefined };
	},
	usePermissionCatalog: () => ({ data: catalog }),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (options: { enabled?: boolean }) => ({
		data: options.enabled ? [{ id: "org-1", name: "Contoso" }] : undefined,
	}),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAnywhere: (permission: string) =>
			permission === "organizations.read" && state.canReadOrganizations,
	}),
}));

import { useTraceNames } from "./useTraceNames";

beforeEach(() => {
	state.canReadOrganizations = true;
	state.accessMapUserId = undefined;
});

describe("useTraceNames", () => {
	it("names roles, organizations and permissions from the subject's access and the catalog", () => {
		const { result } = renderHook(() => useTraceNames("user-1"));

		expect(state.accessMapUserId).toBe("user-1");
		expect(result.current.names.role("role-helpdesk")).toBe("Helpdesk");
		expect(result.current.names.role("role-other")).toBeUndefined();
		expect(result.current.names.organization("org-1")).toBe("Contoso");
		expect(result.current.names.organization("org-2")).toBe("Fabrikam");
		expect(result.current.names.permission("tables.read")).toBe(
			"Read Tables",
		);
		expect(result.current.names.permission("*")).toBe("All Permissions");
		expect([...result.current.organizationNames]).toEqual([
			["org-1", "Contoso"],
			["org-2", "Fabrikam"],
		]);
	});

	it("names only the subject's reach without organizations.read", () => {
		state.canReadOrganizations = false;
		const { result } = renderHook(() => useTraceNames("user-1"));

		expect(result.current.names.organization("org-1")).toBeUndefined();
		expect(result.current.names.organization("org-2")).toBe("Fabrikam");
	});

	it("names nothing from a subject it doesn't have", () => {
		const { result } = renderHook(() => useTraceNames(undefined));

		expect(result.current.names.role("role-helpdesk")).toBeUndefined();
		expect(result.current.names.organization("org-1")).toBe("Contoso");
	});
});
