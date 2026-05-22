import { useEffect } from "react";
import type { AuthUser } from "@/contexts/AuthContext";
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];

export function getPlatformAdminOrgId(
	organizations: Organization[] | undefined,
	currentUser: AuthUser | null | undefined,
): string | null {
	const providerOrg = organizations?.find((org) => org.is_provider);
	return providerOrg?.id ?? (currentUser?.isSuperuser ? currentUser.organizationId : null);
}

export function getEffectiveUserOrgId(
	isPlatformAdmin: boolean,
	orgId: string,
	platformAdminOrgId: string | null,
): string {
	return isPlatformAdmin ? platformAdminOrgId || orgId : orgId;
}

export function usePlatformAdminOrgSelection({
	organizations,
	currentUser,
	isPlatformAdmin,
	setIsPlatformAdmin,
	orgId,
	setOrgId,
}: {
	organizations: Organization[] | undefined;
	currentUser: AuthUser | null | undefined;
	isPlatformAdmin: boolean;
	setIsPlatformAdmin: (value: boolean) => void;
	orgId: string;
	setOrgId: (value: string) => void;
}) {
	const platformAdminOrgId = getPlatformAdminOrgId(organizations, currentUser);
	const effectiveOrgId = getEffectiveUserOrgId(
		isPlatformAdmin,
		orgId,
		platformAdminOrgId,
	);

	useEffect(() => {
		if (isPlatformAdmin && platformAdminOrgId && orgId !== platformAdminOrgId) {
			setOrgId(platformAdminOrgId);
		}
	}, [isPlatformAdmin, orgId, platformAdminOrgId, setOrgId]);

	const handleUserTypeChange = (value: string) => {
		const isAdmin = value === "platform";
		setIsPlatformAdmin(isAdmin);
		if (isAdmin && platformAdminOrgId) {
			setOrgId(platformAdminOrgId);
		} else if (!isAdmin && orgId === platformAdminOrgId) {
			setOrgId("");
		}
	};

	return { effectiveOrgId, handleUserTypeChange };
}
