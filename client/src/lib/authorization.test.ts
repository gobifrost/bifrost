import { describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";

import {
	AUTHORIZATION_QUERY_KEY,
	canAnywhere,
	invalidateAuthorization,
	canAt,
	GLOBAL_TARGET,
	meetsRequirement,
	orgTarget,
	type AuthorizationGrant,
	type AuthorizationSummary,
} from "./authorization";

const PROVIDER = "00000000-0000-0000-0000-000000000002";
const HOME = "11111111-1111-1111-1111-111111111111";
const ORG_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const ORG_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

function summary(
	grants: AuthorizationGrant[],
	overrides: Partial<AuthorizationSummary> = {},
): AuthorizationSummary {
	return {
		is_platform_admin: false,
		home_organization_id: HOME,
		provider_organization_id: PROVIDER,
		base_role: { id: "role", name: "User" },
		grants,
		...overrides,
	};
}

function grant(
	permission: string,
	kind: AuthorizationGrant["boundary"]["kind"],
	organizationId: string | null = null,
): AuthorizationGrant {
	return {
		permission,
		boundary: { kind, organization_id: organizationId },
	};
}

describe("canAt", () => {
	it("allows a Platform Admin everything except secrets.read", () => {
		const admin = summary([], { is_platform_admin: true });

		expect(canAt(admin, "users.readwrite", orgTarget(ORG_A))).toBe(true);
		expect(canAt(admin, "roles.readwrite", GLOBAL_TARGET)).toBe(true);
		expect(canAt(admin, "secrets.read", orgTarget(ORG_A))).toBe(false);
		expect(canAnywhere(admin, "secrets.read")).toBe(false);
	});

	it("does not let an admin's wildcard grant satisfy secrets.read", () => {
		const admin = summary([grant("*", "platform")], {
			is_platform_admin: true,
		});

		expect(canAt(admin, "users.readwrite", orgTarget(ORG_A))).toBe(true);
		expect(canAt(admin, "roles.readwrite", GLOBAL_TARGET)).toBe(true);
		expect(canAt(admin, "secrets.read", orgTarget(ORG_A))).toBe(false);
		expect(canAt(admin, "secrets.read", GLOBAL_TARGET)).toBe(false);
		expect(canAnywhere(admin, "secrets.read")).toBe(false);
	});

	it("gives an admin who holds Secrets Reader secrets.read only where it applies", () => {
		const admin = summary(
			[
				grant("*", "platform"),
				grant("secrets.read", "platform"),
				grant("secrets.read", "managed_organizations"),
				grant("secrets.read", "organization", PROVIDER),
			],
			{ is_platform_admin: true },
		);

		expect(canAt(admin, "secrets.read", GLOBAL_TARGET)).toBe(true);
		expect(canAt(admin, "secrets.read", orgTarget(ORG_A))).toBe(true);
		expect(canAt(admin, "secrets.read", orgTarget(PROVIDER))).toBe(true);
		expect(canAnywhere(admin, "secrets.read")).toBe(true);
	});

	it("decides secrets.read for an admin by explicit grants", () => {
		const admin = summary([grant("secrets.read", "organization", ORG_A)], {
			is_platform_admin: true,
		});

		expect(canAt(admin, "secrets.read", orgTarget(ORG_A))).toBe(true);
		expect(canAt(admin, "secrets.read", orgTarget(ORG_B))).toBe(false);
	});

	it("counts base-role grants only at the home organization", () => {
		const authz = summary([grant("users.read", "home", HOME)]);

		expect(canAt(authz, "users.read", orgTarget(HOME))).toBe(true);
		expect(canAt(authz, "users.read", orgTarget(ORG_A))).toBe(false);
		expect(canAt(authz, "users.read", GLOBAL_TARGET)).toBe(false);
	});

	it("never counts base-role grants for a Global user's home", () => {
		const authz = summary([grant("users.read", "home", null)], {
			home_organization_id: null,
		});

		expect(canAt(authz, "users.read", GLOBAL_TARGET)).toBe(false);
	});

	it("covers exactly the named organization", () => {
		const authz = summary([grant("users.read", "organization", ORG_A)]);

		expect(canAt(authz, "users.read", orgTarget(ORG_A))).toBe(true);
		expect(canAt(authz, "users.read", orgTarget(ORG_B))).toBe(false);
		expect(canAt(authz, "users.read", GLOBAL_TARGET)).toBe(false);
	});

	it("covers every customer organization for managed_organizations", () => {
		const authz = summary([grant("users.read", "managed_organizations")]);

		expect(canAt(authz, "users.read", orgTarget(ORG_A))).toBe(true);
		expect(canAt(authz, "users.read", orgTarget(HOME))).toBe(true);
		expect(canAt(authz, "users.read", orgTarget(PROVIDER))).toBe(false);
		expect(canAt(authz, "users.read", GLOBAL_TARGET)).toBe(false);
	});

	it("covers only Global targets for platform", () => {
		const authz = summary([grant("roles.read", "platform")]);

		expect(canAt(authz, "roles.read", GLOBAL_TARGET)).toBe(true);
		expect(canAt(authz, "roles.read", orgTarget(ORG_A))).toBe(false);
	});

	it("matches the permission exactly", () => {
		const authz = summary([grant("users.read", "managed_organizations")]);

		expect(canAt(authz, "users.readwrite", orgTarget(ORG_A))).toBe(false);
	});

	it("allows nothing before the summary loads", () => {
		expect(canAt(undefined, "users.read", orgTarget(ORG_A))).toBe(false);
		expect(canAnywhere(undefined, "users.read")).toBe(false);
	});
});

describe("orgTarget", () => {
	it("treats a missing organization as Global", () => {
		expect(orgTarget(null)).toEqual(GLOBAL_TARGET);
		expect(orgTarget(undefined)).toEqual(GLOBAL_TARGET);
		expect(orgTarget(ORG_A)).toEqual({ kind: "org", id: ORG_A });
	});
});

describe("canAnywhere and meetsRequirement", () => {
	const operator = summary([
		grant("users.read", "managed_organizations"),
		grant("organizations.read", "managed_organizations"),
	]);

	it("holds a permission granted at any boundary", () => {
		expect(canAnywhere(operator, "users.read")).toBe(true);
		expect(canAnywhere(operator, "roles.read")).toBe(false);
	});

	it("requires a platform boundary for a global requirement", () => {
		expect(meetsRequirement(operator, { permission: "users.read" })).toBe(
			true,
		);
		expect(
			meetsRequirement(operator, {
				permission: "users.read",
				at: "global",
			}),
		).toBe(false);
		expect(
			meetsRequirement(summary([grant("roles.read", "platform")]), {
				permission: "roles.read",
				at: "global",
			}),
		).toBe(true);
	});
});

describe("invalidateAuthorization", () => {
	it("invalidates every user's cached summary", async () => {
		const client = new QueryClient();
		client.setQueryData([...AUTHORIZATION_QUERY_KEY, "user-1"], {});

		await invalidateAuthorization(client);

		expect(
			client.getQueryState([...AUTHORIZATION_QUERY_KEY, "user-1"])
				?.isInvalidated,
		).toBe(true);
	});
});
