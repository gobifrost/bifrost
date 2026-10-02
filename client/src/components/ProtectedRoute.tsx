import { useAuth } from "@/contexts/AuthContext";
import { NoAccess } from "@/components/NoAccess";
import { PageLoader } from "@/components/PageLoader";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { PermissionRequirement } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";

interface ProtectedRouteProps {
	children: React.ReactNode;
	requirePlatformAdmin?: boolean;
	requireOrgUser?: boolean;
	requirePermission?: PermissionRequirement;
}

/**
 * Protected route component that checks user roles
 *
 * @param requirePlatformAdmin - Route requires PlatformAdmin role
 * @param requireOrgUser - Route requires OrgUser role (or PlatformAdmin)
 * @param requirePermission - Route requires a permission (held anywhere,
 *   or platform-wide with `at: "global"`), from the caller's authorization
 */
export function ProtectedRoute({
	children,
	requirePlatformAdmin = false,
	requireOrgUser = false,
	requirePermission,
}: ProtectedRouteProps) {
	const { isAuthenticated, isPlatformAdmin, isOrgUser, isLoading, hasRole } =
		useAuth();
	const authorization = useAuthorization();

	// Wait for auth to load
	if (isLoading) {
		return <PageLoader message="Loading access…" size="sm" />;
	}

	// Authentication recovery belongs to AuthProvider, not the role-denied view.
	if (!isAuthenticated)
		return <PageLoader message="Opening sign in…" size="sm" />;

	// Check for PlatformAdmin requirement
	if (requirePlatformAdmin && !isPlatformAdmin) {
		return (
			<NoAccess
				embedded
				message="You need platform administrator access to view this page. Contact your administrator if you need access."
			/>
		);
	}

	if (requirePermission) {
		if (authorization.isLoading) {
			return <PageLoader message="Loading access…" size="sm" />;
		}
		if (authorization.isError && !authorization.authorization) {
			return (
				<Alert variant="destructive" className="mx-auto mt-6 max-w-lg">
					<AlertTitle>Your access could not be checked</AlertTitle>
					<AlertDescription className="space-y-3">
						<p>Check your connection and try again.</p>
						<Button
							variant="outline"
							className="min-h-11"
							disabled={authorization.isFetching}
							onClick={() => void authorization.refetch()}
						>
							Try again
						</Button>
					</AlertDescription>
				</Alert>
			);
		}
		if (!authorization.meets(requirePermission)) {
			return (
				<NoAccess
					embedded
					message="Your roles don't include access to this page. Contact your administrator if you need access."
				/>
			);
		}
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
