const LIGHT_CANVAS = "#f7f9fa";
const LIGHT_FOREGROUND = "#ffffff";
const DARK_CANVAS = "#08090b";
const DARK_FOREGROUND = "#06222a";

const DEFAULT_LIGHT_PRIMARY = "#087f86";
const DEFAULT_DARK_PRIMARY = "#2fd4d4";

const BRIDGE_STOPS = [
	"#ff5a5a",
	"#ffb24d",
	"#ffe24d",
	"#3ad17a",
	"#2fd4d4",
	"#4d6bff",
	"#7a6bff",
	"#d96bff",
] as const;

const BLACK = { r: 0, g: 0, b: 0 };
const WHITE = { r: 255, g: 255, b: 255 };

const CONTRAST_THRESHOLD = 4.5;
const MIX_STEPS = 28;
const HOVER_SHIFT = 0.08;

type RGB = {
	r: number;
	g: number;
	b: number;
};

export interface BrandPaletteTheme {
	primary: string;
	primaryHover: string;
	primaryForeground: string;
	ring: string;
	activityGradient: string;
}

export interface BrandPalette {
	light: BrandPaletteTheme;
	dark: BrandPaletteTheme;
	isCustom: boolean;
}

const DEFAULT_ACTIVITY_GRADIENT = `linear-gradient(90deg, ${BRIDGE_STOPS.join(", ")})`;

function clampByte(value: number): number {
	return Math.min(255, Math.max(0, Math.round(value)));
}

function clampUnit(value: number): number {
	return Math.min(1, Math.max(0, value));
}

function parseHexColor(value: string): RGB | null {
	const match = value.trim().match(/^#([0-9a-fA-F]{6})$/);
	if (!match) return null;
	const hex = match[1];
	return {
		r: Number.parseInt(hex.slice(0, 2), 16),
		g: Number.parseInt(hex.slice(2, 4), 16),
		b: Number.parseInt(hex.slice(4, 6), 16),
	};
}

function rgbToHex(color: RGB): string {
	return `#${[color.r, color.g, color.b]
		.map((channel) => clampByte(channel).toString(16).padStart(2, "0"))
		.join("")}`;
}

function mixRgb(start: RGB, end: RGB, ratio: number): RGB {
	const t = clampUnit(ratio);
	return {
		r: start.r + (end.r - start.r) * t,
		g: start.g + (end.g - start.g) * t,
		b: start.b + (end.b - start.b) * t,
	};
}

function mixHex(start: string, end: string, ratio: number): string {
	const startRgb = parseHexColor(start);
	const endRgb = parseHexColor(end);
	if (!startRgb || !endRgb) {
		throw new Error("mixHex expects valid hex colors");
	}
	return rgbToHex(mixRgb(startRgb, endRgb, ratio));
}

function relativeLuminance(value: string): number {
	const rgb = parseHexColor(value);
	if (!rgb) {
		throw new Error("relativeLuminance expects a valid hex color");
	}

	const linearize = (channel: number) => {
		const normalized = channel / 255;
		return normalized <= 0.04045
			? normalized / 12.92
			: ((normalized + 0.055) / 1.055) ** 2.4;
	};

	const r = linearize(rgb.r);
	const g = linearize(rgb.g);
	const b = linearize(rgb.b);
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
	const luminanceA = relativeLuminance(a);
	const luminanceB = relativeLuminance(b);
	const lighter = Math.max(luminanceA, luminanceB);
	const darker = Math.min(luminanceA, luminanceB);
	return (lighter + 0.05) / (darker + 0.05);
}

function meetsContrast(color: string, surfaces: readonly string[]): boolean {
	return surfaces.every(
		(surface) => contrastRatio(color, surface) >= CONTRAST_THRESHOLD,
	);
}

function findAccessibleMix(
	base: string,
	target: string,
	surfaces: readonly string[],
): { color: string; ratio: number } {
	let low = 0;
	let high = 1;

	for (let step = 0; step < MIX_STEPS; step += 1) {
		const mid = (low + high) / 2;
		const candidate = mixHex(base, target, mid);
		if (meetsContrast(candidate, surfaces)) {
			high = mid;
		} else {
			low = mid;
		}
	}

	return {
		color: mixHex(base, target, high),
		ratio: high,
	};
}

function buildThemePalette(
	base: string,
	direction: "darken" | "lighten",
	canvas: string,
	foreground: string,
): BrandPaletteTheme {
	const target = direction === "darken" ? rgbToHex(BLACK) : rgbToHex(WHITE);
	const primary = findAccessibleMix(base, target, [canvas, foreground]);
	const hoverRatio = clampUnit(primary.ratio + HOVER_SHIFT);
	const primaryHover = mixHex(base, target, hoverRatio);
	const complementaryTarget = target === "#000000" ? "#ffffff" : "#000000";

	const tint1 = mixHex(primary.color, target, 0.18);
	const tint2 = mixHex(primary.color, target, 0.36);
	const tint3 = mixHex(primary.color, target, 0.54);
	const tint4 = mixHex(primary.color, target, 0.72);
	const shade1 = mixHex(primary.color, complementaryTarget, 0.12);
	const shade2 = mixHex(primary.color, complementaryTarget, 0.24);

	return {
		primary: primary.color,
		primaryHover,
		primaryForeground: foreground,
		ring: primary.color,
		activityGradient: `linear-gradient(90deg, ${[
			tint4,
			tint3,
			tint2,
			tint1,
			primary.color,
			primaryHover,
			shade1,
			shade2,
		].join(", ")})`,
	};
}

function buildDefaultPalette(): BrandPalette {
	return {
		light: {
			primary: DEFAULT_LIGHT_PRIMARY,
			primaryHover: "#056a70",
			primaryForeground: LIGHT_FOREGROUND,
			ring: DEFAULT_LIGHT_PRIMARY,
			activityGradient: DEFAULT_ACTIVITY_GRADIENT,
		},
		dark: {
			primary: DEFAULT_DARK_PRIMARY,
			primaryHover: "#63e1e1",
			primaryForeground: DARK_FOREGROUND,
			ring: DEFAULT_DARK_PRIMARY,
			activityGradient: DEFAULT_ACTIVITY_GRADIENT,
		},
		isCustom: false,
	};
}

export function createBrandPalette(primaryColor?: string | null): BrandPalette {
	if (!primaryColor) {
		return buildDefaultPalette();
	}

	const base = parseHexColor(primaryColor);
	if (!base) {
		return buildDefaultPalette();
	}

	const normalized = rgbToHex(base);
	return {
		light: buildThemePalette(
			normalized,
			"darken",
			LIGHT_CANVAS,
			LIGHT_FOREGROUND,
		),
		dark: buildThemePalette(
			normalized,
			"lighten",
			DARK_CANVAS,
			DARK_FOREGROUND,
		),
		isCustom: true,
	};
}
