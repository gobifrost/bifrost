import type { components } from "@/lib/v1";

import { useApiQuery } from "./use-api-query";

export type Organization = components["schemas"]["OrganizationPublic"];

export interface UseOrganizationsOptions {
	/** Do not issue a request until the calling app is ready to show organizations. */
	enabled?: boolean;
	/** Include disabled organizations when the caller is authorized to view them. */
	includeInactive?: boolean;
}

export interface UseOrganizationsResult {
	data: Organization[] | null;
	loading: boolean;
	error: Error | null;
	refetch: () => Promise<void>;
}

/**
 * Lists the organizations visible to the current caller and provider scope.
 * Server authorization remains authoritative; a 403 is returned as `error`.
 */
export function useOrganizations(
	{ enabled = true, includeInactive = false }: UseOrganizationsOptions = {},
): UseOrganizationsResult {
	return useApiQuery<Organization[]>(
		`/api/organizations?include_inactive=${includeInactive}`,
		{ enabled },
	);
}
