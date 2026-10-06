/** Plain words for permissions: what a person can do, not the wire vocabulary. */

const ACTION_WORDS: Record<string, string> = {
	read: "view",
	readwrite: "manage",
	execute: "run",
};

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

/** "read" → "view", "readwrite.all" → "manage all". */
export function actionWord(action: string): string {
	const all = action.endsWith(ALL_SUFFIX);
	const base = all ? action.slice(0, -ALL_SUFFIX.length) : action;
	const word = ACTION_WORDS[base] ?? base;
	return all ? `${word} all` : word;
}

/** "roles.readwrite" → "manage", "agentruns.read.all" → "view all". */
export function permissionActionWord(permission: string): string {
	const { action, all } = permissionParts(permission);
	return actionWord(all ? `${action}${ALL_SUFFIX}` : action);
}
