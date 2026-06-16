import { useOrgScope } from "@/contexts/OrgScopeContext";

/**
 * The literal product name used wherever no custom Application Name branding
 * is set. Defined once so the fallback string lives in a single place.
 */
export const DEFAULT_APPLICATION_NAME = "Bifrost";

/**
 * Resolve a custom application name against the default.
 *
 * Returns the trimmed custom name when one is set, otherwise
 * {@link DEFAULT_APPLICATION_NAME}. Kept as a pure function (separate from the
 * hook) so it can be unit-tested and reused outside React.
 */
export function resolveApplicationName(
	applicationName: string | null | undefined,
): string {
	const trimmed = applicationName?.trim();
	return trimmed ? trimmed : DEFAULT_APPLICATION_NAME;
}

/**
 * Hook returning the product name to display in the UI — the custom branding
 * Application Name when set, otherwise the default ("Bifrost"). Backed by the
 * public branding fetch in OrgScopeContext, so it resolves on pre-auth screens.
 */
export function useApplicationName(): string {
	const { applicationName } = useOrgScope();
	return resolveApplicationName(applicationName);
}
