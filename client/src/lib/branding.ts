import {
	applyBrandingTheme,
	type BrandingSettings,
} from "./app-sdk/branding-theme";

export { applyBrandingTheme } from "./app-sdk/branding-theme";
export type { BrandingSettings } from "./app-sdk/branding-theme";

/**
 * Fetch branding settings from API.
 * Public endpoint - always returns GLOBAL branding.
 */
export async function fetchBranding(): Promise<BrandingSettings | null> {
	try {
		const response = await fetch("/api/branding");

		if (!response.ok) {
			console.warn("Failed to fetch branding, using defaults");
			return null;
		}

		const branding = await response.json();
		return branding;
	} catch {
		return null;
	}
}

/**
 * Initialize branding on app load.
 * Fetches GLOBAL branding and applies theme.
 */
export async function initializeBranding() {
	const branding = await fetchBranding();
	applyBrandingTheme(branding);
	return branding;
}
