/** Permission names as people read them: "Read and Write Users", not the wire vocabulary. */

import type { PermissionCatalogEntry } from "@/services/access";

/** Platform Admin's one permission: everything except reading secrets. */
export const WILDCARD = "*";

const ALL_SUFFIX = ".all";

/** "agentruns.read.all" → { domain: "agentruns", action: "read", all: true }. */
export function permissionParts(permission: string) {
	const all = permission.endsWith(ALL_SUFFIX);
	const body = all ? permission.slice(0, -ALL_SUFFIX.length) : permission;
	const split = body.lastIndexOf(".");
	return {
		domain: body.slice(0, split),
		action: body.slice(split + 1),
		all,
	};
}

/**
 * "users.readwrite" → "Read and Write Users": the catalog's Graph-style name,
 * from the permission's domain entry. The wildcard is "All Permissions"; a
 * permission the catalog doesn't name shows as written.
 */
export function permissionDisplayName(
	permission: string,
	entry: PermissionCatalogEntry | undefined,
): string {
	if (permission === WILDCARD) return "All Permissions";
	return entry?.names[permission] ?? permission;
}
