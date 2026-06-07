/**
 * Parse a `?from=solution:{id}` query param. Entity pages reached from a
 * Solution detail view use this to offer a "Back to Solution" link instead of
 * their default list back-link. Returns the solution id, or null.
 */
export function parseSolutionFrom(search: string): string | null {
	const params = new URLSearchParams(search);
	const from = params.get("from");
	if (from && from.startsWith("solution:")) {
		const id = from.slice("solution:".length);
		return id || null;
	}
	return null;
}
