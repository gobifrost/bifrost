import { describe, expect, it } from "vitest";

import type { PermissionCatalogEntry } from "@/services/access";

import { permissionDisplayName, permissionParts } from "./permission-words";

const users: PermissionCatalogEntry = {
	domain: "users",
	title: "Users",
	area: "Identity & Access",
	description: "",
	who_should_hold: "",
	actions: ["read", "readwrite"],
	names: {
		"users.read": "Read Users",
		"users.readwrite": "Read and Write Users",
	},
	privileged: ["users.readwrite"],
	scope: "per_organization",
	enforced: true,
};

describe("permissionParts", () => {
	it("splits a dotted domain from its action", () => {
		expect(permissionParts("users.lifecycle.readwrite")).toEqual({
			domain: "users.lifecycle",
			action: "readwrite",
			all: false,
		});
	});

	it("keeps the .all extension apart from the action", () => {
		expect(permissionParts("agentruns.read.all")).toEqual({
			domain: "agentruns",
			action: "read",
			all: true,
		});
	});
});

describe("permissionDisplayName", () => {
	it("uses the catalog's Graph-style name", () => {
		expect(permissionDisplayName("users.readwrite", users)).toBe(
			"Read and Write Users",
		);
		expect(permissionDisplayName("users.read", users)).toBe("Read Users");
	});

	it("names Platform Admin's wildcard All Permissions", () => {
		expect(permissionDisplayName("*", undefined)).toBe("All Permissions");
	});

	it("shows a permission the catalog doesn't name as written", () => {
		expect(permissionDisplayName("users.read.all", users)).toBe(
			"users.read.all",
		);
		expect(permissionDisplayName("tables.read", undefined)).toBe(
			"tables.read",
		);
	});
});
