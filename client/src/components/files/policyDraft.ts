import type { FilePolicy } from "@/services/filePolicies";

/** The folder prefix that governs a path (drop the trailing filename). */
export function prefixForPath(path: string): string {
	if (!path) return "";
	if (path.endsWith("/")) return path;
	const parts = path.split("/");
	parts.pop();
	return parts.length === 0 ? "" : `${parts.join("/")}/`;
}

/** A blank policy draft for a path that has none yet. */
export function makeDefaultPolicy(
	path: string,
	location: string,
	scope: string | null | undefined,
): FilePolicy {
	return {
		location,
		path: prefixForPath(path),
		organizationId: scope ?? null,
		policies: { policies: [] },
	};
}

/** The longest-prefix policy in `policies` that governs `path`, or null. */
export function bestPolicyForPath(
	policies: FilePolicy[],
	path: string,
	location: string,
): FilePolicy | null {
	const candidates = policies
		.filter((policy) => policy.location === location)
		.filter((policy) => path.startsWith(policy.path))
		.sort((a, b) => b.path.length - a.path.length);
	return candidates[0] ?? null;
}
