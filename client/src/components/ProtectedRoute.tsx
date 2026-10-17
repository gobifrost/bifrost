import { useAuth } from "@/contexts/AuthContext";
import { NoAccess } from "@/components/NoAccess";
import { PageLoader } from "@/components/PageLoader";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getEmbedTokenClaims } from "@/lib/auth-token";
import type { PermissionRequirement } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";

interface ProtectedRouteProps {
	children: React.ReactNode;
	requireOrgUser?: boolean;
	requirePermission?: PermissionRequirement;
	allowHmacFormExecutionResult?: boolean;
}

/**
 * Protected route component that checks user roles
 *
 * @param requireOrgUser - Route requires OrgUser role (or PlatformAdmin)
 * @param requirePermission - Route requires a permission (held anywhere,
 *   or platform-wide with `at: "global"`), from the caller's authorization
 * @param allowHmacFormExecutionResult - Allows the HMAC form execution result
 *   route for its signed embed session. The API still binds that session to
 *   the execution ID before returning data.
 */
export function ProtectedRoute({
	children,
	requireOrgUser = false,
	requirePermission,
	allowHmacFormExecutionResult = false,
}: ProtectedRouteProps) {
	const { isAuthenticated, isPlatformAdmin, isOrgUser, isLoading, hasRole } =
		useAuth();
	const authorization = useAuthorization();
	const embedClaims = allowHmacFormExecutionResult
		? getEmbedTokenClaims()
		: null;
	const hasHmacFormExecutionResultAccess =
		allowHmacFormExecutionResult &&
		hasRole("EmbedUser") &&
		embedClaims?.embed === true &&
		embedClaims.embed_kind === "form" &&
		embedClaims.grant === "hmac";

	// Wait for auth to load
	if (isLoading) {
		return <PageLoader message="Loading access…" size="sm" />;
	}

	// Authentication recovery belongs to AuthProvider, not the role-denied view.
	if (!isAuthenticated)
		return <PageLoader message="Opening sign in…" size="sm" />;

	if (requirePermission && !hasHmacFormExecutionResultAccess) {
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
