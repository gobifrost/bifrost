import { useEffect, useMemo } from "react";

import { createBrandPalette } from "./brand-palette";
import { applyBrandingTheme, type BrandingSettings } from "./branding-theme";
import { useApiQuery } from "./use-api-query";
import { useBifrostContext } from "./provider";

export interface UseBrandingOptions {
	/** Apply the loaded branding CSS variables to the current document. Defaults to true. */
	applyTheme?: boolean;
}

export interface UseBrandingResult {
	data: BrandingSettings | null;
	loading: boolean;
	error: Error | null;
	refetch: () => Promise<void>;
	squareLogoUrl: string | null;
	rectangleLogoUrl: string | null;
	applicationName: string | null;
	primaryColor: string | null;
	palette: ReturnType<typeof createBrandPalette>;
	colors: ReturnType<typeof createBrandPalette>["light"];
}

function resolveLogoUrl(
	url: string | null | undefined,
	baseUrl: string,
): string | null {
	if (!url || !url.startsWith("/")) return url ?? null;
	if (baseUrl === "/") return url;
	return `${baseUrl.replace(/\/$/, "")}${url}`;
}

function resolveBrandingUrls(
	branding: BrandingSettings,
	baseUrl: string,
): BrandingSettings {
	return {
		...branding,
		square_logo_url: resolveLogoUrl(branding.square_logo_url, baseUrl),
		rectangle_logo_url: resolveLogoUrl(
			branding.rectangle_logo_url,
			baseUrl,
		),
	};
}

/**
 * Loads platform branding for a standalone app.
 *
 * Logo URLs returned by the API are rooted at the Bifrost backend rather than
 * the app's own Vite origin, which keeps externally developed apps branded.
 */
export function useBranding({
	applyTheme = true,
}: UseBrandingOptions = {}): UseBrandingResult {
	const { baseUrl, theme } = useBifrostContext();
	const query = useApiQuery<BrandingSettings>("/api/branding");
	const data = useMemo(
		() => (query.data ? resolveBrandingUrls(query.data, baseUrl) : null),
		[baseUrl, query.data],
	);
	const palette = useMemo(
		() => createBrandPalette(data?.primary_color ?? null),
		[data?.primary_color],
	);

	useEffect(() => {
		if (applyTheme && data) applyBrandingTheme(data);
	}, [applyTheme, data]);

	return {
		data,
		loading: query.loading,
		error: query.error,
		refetch: query.refetch,
		squareLogoUrl: data?.square_logo_url ?? null,
		rectangleLogoUrl: data?.rectangle_logo_url ?? null,
		applicationName: data?.application_name ?? null,
		primaryColor: data?.primary_color ?? null,
		palette,
		colors: palette[theme],
	};
}
