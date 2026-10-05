import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchBranding, initializeBranding } from "./branding";

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

describe("fetchBranding", () => {
	it("returns null and warns when the API call fails", async () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(new Response(null, { status: 500 })),
		);

		await expect(fetchBranding()).resolves.toBeNull();
		expect(warn).toHaveBeenCalledWith(
			"Failed to fetch branding, using defaults",
		);
	});
});

describe("initializeBranding", () => {
	it("fetches branding and applies it", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(
				new Response(
					JSON.stringify({
						primary_color: "#3366ff",
						square_logo_url: "https://example.test/logo.svg",
					}),
					{
						status: 200,
						headers: { "content-type": "application/json" },
					},
				),
			),
		);

		const branding = await initializeBranding();

		expect(branding).toEqual({
			primary_color: "#3366ff",
			square_logo_url: "https://example.test/logo.svg",
		});
		expect(
			document.head.querySelector(`style#${BRANDING_STYLE_ID}`),
		).toBeTruthy();
		expect(
			document.documentElement.style.getPropertyValue(
				"--logo-square-url",
			),
		).toBe("url('https://example.test/logo.svg')");
	});
});
