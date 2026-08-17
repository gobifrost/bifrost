import type { components } from "@/lib/v1";

export interface AppInfoValues {
	name: string;
	slug: string;
	description?: string;
	organization_id: string | null;
	access_level: "authenticated" | "everyone" | "role_based";
	role_ids: string[];
}

export function buildApplicationUpdateBody(
	values: AppInfoValues,
	isPlatformAdmin: boolean,
): components["schemas"]["ApplicationUpdate"] {
	return {
		name: values.name,
		slug: values.slug,
		description: values.description || null,
		access_level: values.access_level,
		role_ids: values.role_ids,
		...(isPlatformAdmin
			? { organization_id: values.organization_id }
			: {}),
	};
}

export function buildApplicationOrganizationUpdateBody(
	organizationId: string | null,
): components["schemas"]["ApplicationUpdate"] {
	return { organization_id: organizationId };
}
