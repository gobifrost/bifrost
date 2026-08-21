const STORAGE_PREFIX = "bifrost-authorization-boundary";
export const AUTHORIZATION_BOUNDARY_CHANGED_EVENT =
	"bifrost:authorization-boundary-changed";

export function authorizationBoundaryStorageKey(userId: string): string {
	return `${STORAGE_PREFIX}:${userId}`;
}

export function getSelectedAuthorizationBoundary(
	userId = sessionStorage.getItem("userId"),
): string | undefined {
	if (!userId) return undefined;
	return (
		sessionStorage.getItem(authorizationBoundaryStorageKey(userId)) ??
		undefined
	);
}

export function storeSelectedAuthorizationBoundary(
	userId: string,
	boundary: string,
): void {
	sessionStorage.setItem(authorizationBoundaryStorageKey(userId), boundary);
	window.dispatchEvent(
		new CustomEvent(AUTHORIZATION_BOUNDARY_CHANGED_EVENT, {
			detail: { userId, boundary },
		}),
	);
}
