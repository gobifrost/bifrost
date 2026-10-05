import { describe, expect, it } from "vitest";

import { contrastRatio, createBrandPalette } from "./brand-palette";

const DEFAULT_ACTIVITY_GRADIENT =
	"linear-gradient(90deg, #ff5a5a, #ffb24d, #ffe24d, #3ad17a, #2fd4d4, #4d6bff, #7a6bff, #d96bff)";

describe("createBrandPalette", () => {
	it("returns the Bifrost defaults for missing and invalid input", () => {
		const expected = {
			light: {
				primary: "#087f86",
				primaryHover: "#056a70",
				primaryForeground: "#ffffff",
				ring: "#087f86",
				activityGradient: DEFAULT_ACTIVITY_GRADIENT,
			},
			dark: {
				primary: "#2fd4d4",
				primaryHover: "#63e1e1",
				primaryForeground: "#06222a",
				ring: "#2fd4d4",
				activityGradient: DEFAULT_ACTIVITY_GRADIENT,
			},
			isCustom: false,
		};

		expect(createBrandPalette()).toEqual(expected);
		expect(createBrandPalette(null)).toEqual(expected);
		expect(createBrandPalette("nope")).toEqual(expected);
		expect(createBrandPalette("#123")).toEqual(expected);
	});

	it.each([
		["blue", "#3366ff"],
		["red", "#d7263d"],
		["yellow", "#f2c94c"],
		["near white", "#fefefe"],
		["black", "#050505"],
		["gray", "#808080"],
	])("builds accessible palettes for %s", (_label, color) => {
		const palette = createBrandPalette(color);

		expect(palette.isCustom).toBe(true);
		expect(palette.light.ring).toBe(palette.light.primary);
		expect(palette.dark.ring).toBe(palette.dark.primary);

		expect(
			contrastRatio(palette.light.primary, "#f7f9fa"),
		).toBeGreaterThanOrEqual(4.5);
		expect(
			contrastRatio(
				palette.light.primaryForeground,
				palette.light.primary,
			),
		).toBeGreaterThanOrEqual(4.5);
		expect(
			contrastRatio(
				palette.light.primaryHover,
				palette.light.primaryForeground,
			),
		).toBeGreaterThanOrEqual(4.5);

		expect(
			contrastRatio(palette.dark.primary, "#08090b"),
		).toBeGreaterThanOrEqual(4.5);
		expect(
			contrastRatio(palette.dark.primaryForeground, palette.dark.primary),
		).toBeGreaterThanOrEqual(4.5);
		expect(
			contrastRatio(
				palette.dark.primaryHover,
				palette.dark.primaryForeground,
			),
		).toBeGreaterThanOrEqual(4.5);

		expect(palette.light.activityGradient).toContain(palette.light.primary);
		expect(palette.light.activityGradient).toContain(
			palette.light.primaryHover,
		);
		expect(palette.dark.activityGradient).toContain(palette.dark.primary);
		expect(palette.dark.activityGradient).toContain(
			palette.dark.primaryHover,
		);
	});

	it("keeps custom gradients deterministic and tonal", () => {
		const first = createBrandPalette("#3366ff");
		const second = createBrandPalette("#3366ff");
		const different = createBrandPalette("#ff5a5a");

		expect(first).toEqual(second);
		expect(first.light.activityGradient).not.toBe(
			DEFAULT_ACTIVITY_GRADIENT,
		);
		expect(first.dark.activityGradient).not.toBe(DEFAULT_ACTIVITY_GRADIENT);
		expect(first.light.activityGradient).not.toBe(
			first.dark.activityGradient,
		);
		expect(first.light.activityGradient).not.toBe(
			different.light.activityGradient,
		);
		expect(first.dark.activityGradient).not.toBe(
			different.dark.activityGradient,
		);
	});
});
