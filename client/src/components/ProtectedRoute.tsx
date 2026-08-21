import { useAuth } from "@/contexts/AuthContext";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";
import { NoAccess } from "@/components/NoAccess";

interface ProtectedRouteProps {
	children: React.ReactNode;
	requirePlatformAdmin?: boolean;
	requireOrgUser?: boolean;
	requireCapability?: string;
	requireAnyCapability?: string[];
	requireBoundaryKind?: "platform";
}

/**
 * Protected route component that checks user roles
 *
 * @param requirePlatformAdmin - Route requires PlatformAdmin role
 * @param requireOrgUser - Route requires OrgUser role (or PlatformAdmin)
 */
export function ProtectedRoute({
	children,
	requirePlatformAdmin = false,
	requireOrgUser = false,
	requireCapability,
	requireAnyCapability,
	requireBoundaryKind,
}: ProtectedRouteProps) {
	const { isPlatformAdmin, isOrgUser, isLoading, hasRole } = useAuth();
	const {
		hasSelectedCapability,
		isLoading: isAuthorizationBoundaryLoading,
		selectedTarget,
	} = useAuthorizationBoundary();

	// Wait for auth to load
	if (
		isLoading ||
		((requireCapability || requireAnyCapability) &&
			isAuthorizationBoundaryLoading)
	) {
		return null;
	}

	// Check for PlatformAdmin requirement
	if (requirePlatformAdmin && !isPlatformAdmin) {
		return <NoAccess />;
	}

	if (requireCapability && !hasSelectedCapability(requireCapability)) {
		return <NoAccess />;
	}

	if (
		requireAnyCapability &&
		!requireAnyCapability.some(hasSelectedCapability)
	) {
		return <NoAccess />;
	}

	if (requireBoundaryKind && selectedTarget?.kind !== requireBoundaryKind) {
		return (
			<NoAccess
				title="Choose another working context"
				description="Select Global from Working in to open this platform-wide area."
			/>
		);
	}

	// Check for OrgUser requirement (PlatformAdmin and EmbedUser also have access)
	if (
		requireOrgUser &&
		!isOrgUser &&
		!isPlatformAdmin &&
		!hasRole("EmbedUser")
	) {
		return <NoAccess />;
	}

	return <>{children}</>;
}
