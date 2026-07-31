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
	return {
		canBuild:
			hasPermission && (aiConfigured || (data?.is_platform_admin ?? false)),
		hasPermission,
		aiConfigured,
		isLoading,
		solutions: data?.solutions ?? [],
	};
}
