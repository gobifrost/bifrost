/**
 * Branding utilities for dynamic theming.
 * Applies organization-specific branding (logos and colors) via CSS variables.
 */

import { createBrandPalette } from "./brand-palette";
import type { components } from "@/lib/v1";

export type BrandingSettings = components["schemas"]["BrandingSettings"];

const BRANDING_THEME_STYLE_ID = "bifrost-branding-theme";
const LEGACY_THEME_STYLE_SELECTOR =
	'style[data-theme="dark"], style[data-branding-override]';
const THEME_VARS = [
	"--primary",
	"--primary-foreground",
	"--sidebar-primary",
	"--sidebar-primary-foreground",
	"--ring",
	"--sidebar-ring",
	"--color-primary",
	"--color-primary-foreground",
	"--color-sidebar-primary",
	"--color-sidebar-primary-foreground",
	"--color-ring",
	"--color-sidebar-ring",
] as const;

function getStyleElement(id: string): HTMLStyleElement {
	let style = document.head.querySelector<HTMLStyleElement>(`style#${id}`);
	if (!style) {
		style = document.createElement("style");
		style.id = id;
		document.head.appendChild(style);
	}
	return style;
}

function clearLegacyThemeState(root: HTMLElement) {
	for (const variable of THEME_VARS) {
		root.style.removeProperty(variable);
	}

	for (const legacyStyle of document.head.querySelectorAll(
		LEGACY_THEME_STYLE_SELECTOR,
	)) {
		legacyStyle.remove();
	}
}

function buildBrandingThemeStyles(
	light: ReturnType<typeof createBrandPalette>["light"],
	dark: ReturnType<typeof createBrandPalette>["dark"],
): string {
	return `
:root {
  --bf-brand-primary: ${light.primary};
  --bf-brand-primary-hover: ${light.primaryHover};
  --bf-brand-primary-foreground: ${light.primaryForeground};
  --bf-brand-ring: ${light.ring};
  --bf-brand-activity-gradient: ${light.activityGradient};
  --bf-primary: ${light.primary};
  --bf-primary-hover: ${light.primaryHover};
  --bf-primary-foreground: ${light.primaryForeground};
  --bf-bridge: ${light.activityGradient};
  --bf-bridge-vertical: ${light.activityGradient.replace("90deg", "180deg")};
  --bf-activity-gradient: ${light.activityGradient};
  --primary: var(--bf-brand-primary);
  --primary-foreground: var(--bf-brand-primary-foreground);
  --sidebar-primary: var(--bf-brand-primary);
  --sidebar-primary-foreground: var(--bf-brand-primary-foreground);
  --ring: var(--bf-brand-ring);
  --sidebar-ring: var(--bf-brand-ring);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-ring: var(--ring);
  --color-sidebar-ring: var(--sidebar-ring);
}

:root.dark {
  --bf-brand-primary: ${dark.primary};
  --bf-brand-primary-hover: ${dark.primaryHover};
  --bf-brand-primary-foreground: ${dark.primaryForeground};
  --bf-brand-ring: ${dark.ring};
  --bf-brand-activity-gradient: ${dark.activityGradient};
  --bf-primary: ${dark.primary};
  --bf-primary-hover: ${dark.primaryHover};
  --bf-primary-foreground: ${dark.primaryForeground};
  --bf-bridge: ${dark.activityGradient};
  --bf-bridge-vertical: ${dark.activityGradient.replace("90deg", "180deg")};
  --bf-activity-gradient: ${dark.activityGradient};
  --primary: var(--bf-brand-primary);
  --primary-foreground: var(--bf-brand-primary-foreground);
  --sidebar-primary: var(--bf-brand-primary);
  --sidebar-primary-foreground: var(--bf-brand-primary-foreground);
  --ring: var(--bf-brand-ring);
  --sidebar-ring: var(--bf-brand-ring);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-ring: var(--ring);
  --color-sidebar-ring: var(--sidebar-ring);
}
`.trim();
}

/**
 * Apply branding theme to the document.
 * Updates CSS custom properties for instant visual updates.
 */
export function applyBrandingTheme(branding: BrandingSettings | null) {
	const root = document.documentElement;
	const palette = createBrandPalette(branding?.primary_color ?? null);

	clearLegacyThemeState(root);

	const themeStyles = getStyleElement(BRANDING_THEME_STYLE_ID);
	themeStyles.textContent = buildBrandingThemeStyles(
		palette.light,
		palette.dark,
	);

	// Apply logo URLs as CSS variables (for use in background-image).
	if (branding?.square_logo_url) {
		root.style.setProperty(
			"--logo-square-url",
			`url('${branding.square_logo_url}')`,
		);
	} else {
		root.style.removeProperty("--logo-square-url");
	}

	if (branding?.rectangle_logo_url) {
		root.style.setProperty(
			"--logo-rectangle-url",
			`url('${branding.rectangle_logo_url}')`,
		);
	} else {
		root.style.removeProperty("--logo-rectangle-url");
	}
}
