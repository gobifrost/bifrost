/**
 * Builder capability probe.
 *
 * The builder list endpoint is the capability check: a 403 means the caller
 * lacks `solutions.build`, in which case every builder entry point stays
 * hidden rather than surfacing an error. Any other failure also hides the
 * feature — per the spec the builder fails closed.
 */

import { useQuery } from "@tanstack/react-query";
import {
	BuilderApiError,
	listBuilderTargets,
	listBuilderSolutions,
	type BuilderBlocker,
	type BuilderOrganizationTarget,
	type BuilderSolution,
} from "@/services/builder";

export const builderSolutionsQueryKey = ["builder", "solutions"] as const;

export interface BuilderAccess {
	/** Whether Builder navigation and read/support surfaces should render. */
	canAccessBuilder: boolean;
	/** Whether builder entry points should render at all. */
	canBuild: boolean;
	/** The caller holds the capability, regardless of AI readiness. */
	hasPermission: boolean;
	/** Whether a usable platform AI provider is configured. */
	aiConfigured: boolean;
	/** Whether AI and the sandbox runner are fully connected and enabled. */
	builderReady: boolean;
	/** Actionable readiness failures shown only on administrator surfaces. */
	blockers: BuilderBlocker[];
	/** Whether this caller can deliberately open the support-wide catalog. */
	canViewAll: boolean;
	/** Whether the server says this caller may open the global workspace. */
	canOpenGlobalWorkspace: boolean;
	/** Server-derived admin fact used to keep setup visible while blocked. */
	isPlatformAdmin: boolean;
	/** True while the capability is still being probed — render nothing yet. */
	isLoading: boolean;
	/** The caller's private Solutions, empty when access is denied. */
	solutions: BuilderSolution[];
	/** Exact organization boundaries the caller may select for Builder. */
	organizationTargets: BuilderOrganizationTarget[];
}

export function useBuilderAccess(): BuilderAccess {
	const targetsQuery = useQuery({
		queryKey: ["builder", "targets"],
		queryFn: ({ signal }) => listBuilderTargets({ signal }),
		retry: (failureCount, err) => {
			if (err instanceof BuilderApiError && err.isForbidden) return false;
			return failureCount < 1;
		},
	});
	const solutionsQuery = useQuery({
		queryKey: builderSolutionsQueryKey,
		queryFn: ({ signal }) => listBuilderSolutions({ signal }),
		enabled: targetsQuery.isSuccess,
		retry: (failureCount, err) => {
			// Never retry a capability denial; it is a stable answer.
			if (err instanceof BuilderApiError && err.isForbidden) return false;
			return failureCount < 1;
		},
	});

	const data = targetsQuery.data;
	const hasPermission = !targetsQuery.isLoading && !targetsQuery.error;
	const aiConfigured = data?.ai_configured ?? false;
	const builderReady = data?.builder_ready ?? false;
	const isPlatformAdmin = data?.is_platform_admin ?? false;
	const hasExecutePermission = Boolean(
		data?.can_open_global_workspace ||
			(data?.organizations ?? []).some((target) => target.can_execute),
	);
	return {
		canAccessBuilder: hasPermission && (builderReady || isPlatformAdmin),
		canBuild:
			hasPermission &&
			hasExecutePermission &&
			(builderReady || isPlatformAdmin),
		hasPermission,
		aiConfigured,
		builderReady,
		blockers: data?.builder_blockers ?? [],
		canViewAll: data?.can_view_all ?? false,
		canOpenGlobalWorkspace: data?.can_open_global_workspace ?? false,
		isPlatformAdmin,
		isLoading: targetsQuery.isLoading,
		solutions: solutionsQuery.data?.solutions ?? [],
		organizationTargets: data?.organizations ?? [],
	};
}
