import { afterEach, describe, expect, it, vi } from "vitest";

import { createBrandPalette } from "./brand-palette";

import { applyBrandingTheme } from "./branding-theme";

const BRANDING_STYLE_ID = "bifrost-branding-theme";

function resetDom() {
	document.documentElement.className = "";
	document.documentElement.removeAttribute("style");
	document.head
		.querySelectorAll(
			'style#bifrost-branding-theme, style[data-theme="dark"], style[data-branding-override]',
		)
		.forEach((node) => node.remove());
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
	resetDom();
});

describe("applyBrandingTheme", () => {
	it("applies the generated palette as owned CSS variables and aliases", () => {
		const branding = {
			primary_color: "#3366ff",
			square_logo_url: "https://example.test/logo-square.svg",
			rectangle_logo_url: "https://example.test/logo-rect.svg",
		};
		const palette = createBrandPalette(branding.primary_color);

		applyBrandingTheme(branding);

		const style = document.head.querySelector(`style#${BRANDING_STYLE_ID}`);
		expect(style).toBeTruthy();
		expect(style?.textContent).toContain(
			`--bf-brand-primary: ${palette.light.primary};`,
		);
		expect(style?.textContent).toContain(
			`--bf-primary: ${palette.light.primary};`,
		);
		expect(style?.textContent).toContain(
			`--bf-primary-hover: ${palette.light.primaryHover};`,
		);
		expect(style?.textContent).toContain(
			`--bf-primary-foreground: ${palette.light.primaryForeground};`,
		);
		expect(style?.textContent).toContain(
			`--bf-bridge: ${palette.light.activityGradient};`,
		);
		expect(style?.textContent).toContain(
			`--bf-activity-gradient: ${palette.light.activityGradient};`,
		);
		expect(style?.textContent).toContain(
			`--bf-brand-primary: ${palette.dark.primary};`,
		);
		expect(style?.textContent).toContain(
			`--bf-primary: ${palette.dark.primary};`,
		);
		expect(style?.textContent).toContain(
			`--bf-primary-hover: ${palette.dark.primaryHover};`,
		);
		expect(style?.textContent).toContain(
			`--bf-primary-foreground: ${palette.dark.primaryForeground};`,
		);
		expect(style?.textContent).toContain(
			`--bf-bridge: ${palette.dark.activityGradient};`,
		);
		expect(style?.textContent).toContain(
			`--bf-activity-gradient: ${palette.dark.activityGradient};`,
		);
		expect(style?.textContent).toContain(`:root.dark {`);
		expect(style?.textContent).toContain(
			`--color-primary: var(--primary);`,
		);
		expect(style?.textContent).toContain(
			`--bf-brand-activity-gradient: ${palette.light.activityGradient};`,
		);
		expect(style?.textContent).toContain(
			`--bf-brand-activity-gradient: ${palette.dark.activityGradient};`,
		);
		expect(style?.textContent).not.toContain("!important");

		expect(
			document.documentElement.style.getPropertyValue("--primary"),
		).toBe("");
		expect(
			document.documentElement.style.getPropertyValue(
				"--sidebar-primary",
			),
		).toBe("");
		expect(
			document.documentElement.style.getPropertyValue(
				"--logo-square-url",
			),
		).toBe(`url('${branding.square_logo_url}')`);
		expect(
			document.documentElement.style.getPropertyValue(
				"--logo-rectangle-url",
			),
		).toBe(`url('${branding.rectangle_logo_url}')`);
	});

	it("replaces the existing branding styles instead of duplicating them", () => {
		applyBrandingTheme({ primary_color: "#3366ff" });
		const first = document.head.querySelector(`style#${BRANDING_STYLE_ID}`);
		expect(first).toBeTruthy();

		applyBrandingTheme({ primary_color: "#d7263d" });
		const styles = document.head.querySelectorAll(
			`style#${BRANDING_STYLE_ID}`,
		);

		expect(styles).toHaveLength(1);
		expect(styles[0]?.textContent).toContain("#d7263d");
		expect(styles[0]?.textContent).not.toContain("#3366ff");
	});

	it("falls back to defaults and clears removed logos", () => {
		applyBrandingTheme({
			primary_color: "not-a-color",
			square_logo_url: "https://example.test/logo.svg",
		});

		expect(
			document.head.querySelector(`style#${BRANDING_STYLE_ID}`)
				?.textContent,
		).toContain("#087f86");
		expect(
			document.documentElement.style.getPropertyValue(
				"--logo-square-url",
			),
		).toBe("url('https://example.test/logo.svg')");

		applyBrandingTheme(null);

		const style = document.head.querySelector(`style#${BRANDING_STYLE_ID}`);
		const defaults = createBrandPalette(null);
		expect(style?.textContent).toContain(defaults.light.primary);
		expect(style?.textContent).toContain(defaults.dark.primary);
		expect(
			document.documentElement.style.getPropertyValue(
				"--logo-square-url",
			),
		).toBe("");
		expect(
			document.documentElement.style.getPropertyValue(
				"--logo-rectangle-url",
			),
		).toBe("");
	});

	it("removes legacy theme styles before writing the new CSS variables", () => {
		const legacyDark = document.createElement("style");
		legacyDark.setAttribute("data-theme", "dark");
		document.head.appendChild(legacyDark);
		const legacyOverride = document.createElement("style");
		legacyOverride.setAttribute("data-branding-override", "true");
		document.head.appendChild(legacyOverride);

		applyBrandingTheme({ primary_color: "#3366ff" });

		expect(
			document.head.querySelector('style[data-theme="dark"]'),
		).toBeNull();
		expect(
			document.head.querySelector("style[data-branding-override]"),
		).toBeNull();
	});
});
