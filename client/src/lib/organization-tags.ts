/**
 * Organization tag helpers shared by the Organizations page and the
 * integration mappings tab. Tags compare case-insensitively, matching the
 * server's normalization.
 */

interface Tagged {
	tags?: string[] | null;
}

const tagKey = (tag: string) => tag.toLocaleLowerCase();

/** Every distinct tag across `items`, first spelling kept, sorted by name. */
export function collectOrganizationTags(items: readonly Tagged[]): string[] {
	const byKey = new Map<string, string>();
	for (const item of items) {
		for (const tag of item.tags ?? []) {
			if (!byKey.has(tagKey(tag))) byKey.set(tagKey(tag), tag);
		}
	}
	return [...byKey.values()].sort((a, b) =>
		a.localeCompare(b, undefined, { sensitivity: "base" }),
	);
}

/** True when no filter is selected, or `item` carries any selected tag. */
export function matchesTagFilter(
	item: Tagged,
	selectedTags: readonly string[],
): boolean {
	if (selectedTags.length === 0) return true;
	const wanted = new Set(selectedTags.map(tagKey));
	return (item.tags ?? []).some((tag) => wanted.has(tagKey(tag)));
}
