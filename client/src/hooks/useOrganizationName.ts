import { useCallback } from "react";

import { useAuthorization } from "@/services/authorization";

type OrganizationName = { id: string; name: string };

/** Resolve a scope label without treating an undiscoverable home organization as unknown. */
export function resolveOrganizationName(
	organizationId: string | null | undefined,
	organizations: readonly OrganizationName[] | undefined,
	homeOrganizationId: string | null | undefined,
	homeOrganizationName: string | null | undefined,
	unknownLabel?: string,
): string {
	if (!organizationId) return "Global";
	const directoryName = organizations?.find(
		(organization) => organization.id === organizationId,
	)?.name;
	if (directoryName) return directoryName;
	if (
		organizationId === homeOrganizationId &&
		homeOrganizationName
	) {
		return homeOrganizationName;
	}
	return unknownLabel ?? organizationId;
}

/** Resolve directory names plus the signed-in person's own home-organization label. */
export function useOrganizationName(
	organizations: readonly OrganizationName[] | undefined,
) {
	const { authorization } = useAuthorization();
	return useCallback(
		(
			organizationId: string | null | undefined,
			unknownLabel?: string,
		) =>
			resolveOrganizationName(
				organizationId,
				organizations,
				authorization?.home_organization_id,
				authorization?.home_organization_name,
				unknownLabel,
			),
		[
			authorization?.home_organization_id,
			authorization?.home_organization_name,
			organizations,
		],
	);
}
