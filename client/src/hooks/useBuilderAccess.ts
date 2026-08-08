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
	listBuilderSolutions,
	type BuilderBlocker,
	type BuilderSolution,
} from "@/services/builder";

export const builderSolutionsQueryKey = ["builder", "solutions"] as const;

export interface BuilderAccess {
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
	/** Server-derived admin fact used to keep setup visible while blocked. */
	isPlatformAdmin: boolean;
	/** True while the capability is still being probed — render nothing yet. */
	isLoading: boolean;
	/** The caller's private Solutions, empty when access is denied. */
	solutions: BuilderSolution[];
}

export function useBuilderAccess(): BuilderAccess {
	const { data, isLoading, error } = useQuery({
		queryKey: builderSolutionsQueryKey,
		queryFn: ({ signal }) => listBuilderSolutions({ signal }),
		retry: (failureCount, err) => {
			// Never retry a capability denial; it is a stable answer.
			if (err instanceof BuilderApiError && err.isForbidden) return false;
			return failureCount < 1;
		},
	});

	const hasPermission = !isLoading && !error;
	const aiConfigured = data?.ai_configured ?? false;
	const builderReady = data?.builder_ready ?? false;
	const isPlatformAdmin = data?.is_platform_admin ?? false;
	return {
		canBuild: hasPermission && (builderReady || isPlatformAdmin),
		hasPermission,
		aiConfigured,
		builderReady,
		blockers: data?.builder_blockers ?? [],
		canViewAll: data?.can_view_all ?? false,
		isPlatformAdmin,
		isLoading,
		solutions: data?.solutions ?? [],
	};
}
