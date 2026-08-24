import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";

export function organizationBoundary(
	organizationId: string | null | undefined,
): string {
	return organizationId ? `organization:${organizationId}` : "platform";
}

export function authorizationHeaders(boundary: string) {
	return { "X-Bifrost-Boundary": boundary } as const;
}

export function useAdministrativeBoundary(
	_requiredCapability?: string,
): string | undefined {
	return useAuthorizationBoundary().selectedBoundary;
}
