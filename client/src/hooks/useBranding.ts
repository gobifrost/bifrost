/**
 * Branding Hook
 *
 * Handles loading and caching of branding assets (logos, colors).
 * Separated from org scope to allow independent loading and caching.
 */

import { useState, useEffect, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import {
	initializeBranding,
	applyBrandingTheme,
	type BrandingSettings,
} from "@/lib/branding";
import type { components } from "@/lib/v1";

// Re-export types for convenience
export type BrandingSettings_API = components["schemas"]["BrandingSettings"];
export type BrandingUpdateRequest =
	components["schemas"]["BrandingUpdateRequest"];

export interface BrandingState {
	/** Whether branding data has been loaded */
	brandingLoaded: boolean;
	/** Whether logo images have been preloaded */
	logoLoaded: boolean;
	/** URL for square logo (sidebar icon) */
	squareLogoUrl: string | null;
	/** URL for rectangle logo (header) */
	rectangleLogoUrl: string | null;
	/** Custom product name, or null when unset (falls back to the default) */
	applicationName: string | null;
	/** Refresh branding data (e.g., after upload) */
	refreshBranding: () => void;
}

/**
 * Preload an image and return when loaded
 */
function preloadImage(url: string, timeout = 5000): Promise<void> {
	return new Promise((resolve) => {
		const img = new Image();
		img.onload = () => resolve();
		img.onerror = () => resolve(); // Continue even on error
		img.src = url;
		// Timeout fallback
		setTimeout(() => resolve(), timeout);
	});
}

/**
 * Get current branding settings (public endpoint)
 */
export async function getBranding(): Promise<BrandingSettings_API | null> {
	try {
		const { data } = await apiClient.GET("/api/branding", {});
		return data || null;
	} catch {
		return null;
	}
}

/**
 * Update branding settings (superuser only)
 */
export async function updateBranding(
	settings: BrandingUpdateRequest,
): Promise<BrandingSettings_API> {
	const { data, error } = await apiClient.PUT("/api/branding", {
		body: settings,
	});

	if (error) {
		throw new Error(
			typeof error === "object" && error !== null && "message" in error
				? (error as { message?: string }).message ||
						"Failed to update branding"
				: "Failed to update branding",
		);
	}

	return data;
}

/**
 * Upload logo (superuser only)
 */
export async function uploadLogo(
	type: "square" | "rectangle",
	file: File,
): Promise<void> {
	const formData = new FormData();
	formData.append("file", file);

	const { error } = await apiClient.POST("/api/branding/logo/{logo_type}", {
		params: { path: { logo_type: type } },
		body: formData as unknown as { file: string },
	});

	if (error) {
		throw new Error(
			`Failed to upload ${type} logo: ${
				typeof error === "object" &&
				error !== null &&
				"message" in error
					? (error as { message?: string }).message
					: "Unknown error"
			}`,
		);
	}
}

/**
 * Reset logo to default (superuser only)
 */
export async function resetLogo(
	type: "square" | "rectangle",
): Promise<BrandingSettings_API> {
	const { data, error } = await apiClient.DELETE(
		"/api/branding/logo/{logo_type}",
		{
			params: { path: { logo_type: type } },
		},
	);

	if (error) {
		throw new Error(
			`Failed to reset ${type} logo: ${
				typeof error === "object" &&
				error !== null &&
				"message" in error
					? (error as { message?: string }).message
					: "Unknown error"
			}`,
		);
	}

	return data;
}

/**
 * Reset primary color to default (superuser only)
 */
export async function resetColor(): Promise<BrandingSettings_API> {
	const { data, error } = await apiClient.DELETE("/api/branding/color", {});

	if (error) {
		throw new Error(
			`Failed to reset primary color: ${
				typeof error === "object" &&
				error !== null &&
				"message" in error
					? (error as { message?: string }).message
					: "Unknown error"
			}`,
		);
	}

	return data;
}

/**
 * Reset application name to default (superuser only)
 */
export async function resetApplicationName(): Promise<BrandingSettings_API> {
	const { data, error } = await apiClient.DELETE(
		"/api/branding/application-name",
		{},
	);

	if (error) {
		throw new Error(
			`Failed to reset application name: ${
				typeof error === "object" &&
				error !== null &&
				"message" in error
					? (error as { message?: string }).message
					: "Unknown error"
			}`,
		);
	}

	return data;
}

/**
 * Reset all branding to defaults (superuser only)
 */
export async function resetAllBranding(): Promise<BrandingSettings_API> {
	const { data, error } = await apiClient.DELETE("/api/branding", {});

	if (error) {
		throw new Error(
			`Failed to reset branding: ${
				typeof error === "object" &&
				error !== null &&
				"message" in error
					? (error as { message?: string }).message
					: "Unknown error"
			}`,
		);
	}

	return data;
}

/**
 * Hook for loading and managing branding assets.
 *
 * @example
 * ```tsx
 * function Header() {
 *   const { rectangleLogoUrl, brandingLoaded } = useBranding();
 *
 *   if (!brandingLoaded) return <Skeleton />;
 *
 *   return <img src={rectangleLogoUrl || "/logo.svg"} />;
 * }
 * ```
 */
export function useBranding(): BrandingState {
	const queryClient = useQueryClient();
	const [logoLoaded, setLogoLoaded] = useState(false);
	const [squareLogoUrl, setSquareLogoUrl] = useState<string | null>(null);
	const [rectangleLogoUrl, setRectangleLogoUrl] = useState<string | null>(
		null,
	);
	const [brandingApplied, setBrandingApplied] = useState(false);

	// Fetch branding data (public endpoint)
	const { data: branding, isLoading } = $api.useQuery(
		"get",
		"/api/branding",
		{},
		{
			staleTime: 5 * 60 * 1000, // 5 minutes - cache branding longer
			retry: 1,
		},
	);

	// Process branding data when it changes
	useEffect(() => {
		async function processBranding() {
			setLogoLoaded(false);

			if (!branding) {
				// No branding data - apply defaults
				await initializeBranding();
				setBrandingApplied(true);
				setLogoLoaded(true);
				return;
			}

			// Preload logos
			const preloadPromises: Promise<void>[] = [];

			if (branding.rectangle_logo_url) {
				preloadPromises.push(preloadImage(branding.rectangle_logo_url));
			}

			if (branding.square_logo_url) {
				preloadPromises.push(preloadImage(branding.square_logo_url));
			}

			await Promise.all(preloadPromises);

			// Store logo URLs
			setSquareLogoUrl(branding.square_logo_url || null);
			setRectangleLogoUrl(branding.rectangle_logo_url || null);

			// Apply theme colors
			applyBrandingTheme(branding as BrandingSettings);
			setBrandingApplied(true);

			setLogoLoaded(true);
		}

		if (!isLoading) {
			processBranding();
		}
	}, [branding, isLoading]);

	const refreshBranding = useCallback(() => {
		setBrandingApplied(false);
		queryClient.invalidateQueries({ queryKey: ["get", "/api/branding"] });
	}, [queryClient]);

	return {
		brandingLoaded: !isLoading && brandingApplied,
		logoLoaded,
		squareLogoUrl,
		rectangleLogoUrl,
		applicationName: branding?.application_name || null,
		refreshBranding,
	};
}
