import { useOrganizations } from "@/hooks/useOrganizations";
import type { TraceNames } from "@/lib/access-trace";
import { permissionDisplayName, permissionParts } from "@/lib/permission-words";
import { usePermissionCatalog, useUserAccessMap } from "@/services/access";
import { useAuthorization } from "@/services/authorization";

/**
 * Names for an access trace of `subjectId` (the user or identity it ran
 * as): roles from their access map, organizations from the organizations
 * list and their reach, permissions by their Graph-style names.
 */
export function useTraceNames(subjectId: string | undefined): {
	names: TraceNames;
	organizationNames: Map<string, string>;
} {
	const authorization = useAuthorization();
	const map = useUserAccessMap(subjectId).data;
	const catalog = usePermissionCatalog().data;
	const organizations = useOrganizations({
		enabled: authorization.canAnywhere("organizations.read"),
	}).data;

	const organizationNames = new Map<string, string>();
	for (const { id, name } of organizations ?? [])
		organizationNames.set(id, name);
	for (const place of map?.reach ?? [])
		if (place.organization_id && place.organization_name)
			organizationNames.set(
				place.organization_id,
				place.organization_name,
			);

	const roleNames = new Map<string, string>();
	for (const row of map?.rows ?? [])
		for (const grant of row.grants)
			for (const source of grant.sources)
				roleNames.set(source.role_id, source.role_name);

	return {
		names: {
			role: (id) => roleNames.get(id),
			organization: (id) => organizationNames.get(id),
			permission: (permission) =>
				permissionDisplayName(
					permission,
					catalog?.find(
						(entry) =>
							entry.domain === permissionParts(permission).domain,
					),
				),
		},
		organizationNames,
	};
}
