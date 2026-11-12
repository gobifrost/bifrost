import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDocumentChrome, setPlatformFavicon } from "./useDocumentChrome";

const SVG_DATA_URL = "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=";
const PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==";

function faviconLink(): HTMLLinkElement {
	const link = document.querySelector<HTMLLinkElement>("link[rel='icon']");
	if (!link) throw new Error("favicon link missing from test DOM");
	return link;
}

describe("useDocumentChrome", () => {
	beforeEach(() => {
		// Rebuild <head> first — clearing innerHTML drops the <title> element
		// that document.title writes through, so set the title afterward.
		document.head.innerHTML = "";
		const link = document.createElement("link");
		link.setAttribute("rel", "icon");
		link.setAttribute("type", "image/svg+xml");
		link.setAttribute("href", "/logo.svg");
		document.head.appendChild(link);
		document.title = "Bifrost Integrations";
	});

	afterEach(() => {
		document.head.innerHTML = "";
	});

	describe("title", () => {
		it("sets the document title and restores it on unmount", () => {
			const { unmount } = renderHook(() =>
				useDocumentChrome({ title: "My App | Bifrost", logo: null }),
			);
			expect(document.title).toBe("My App | Bifrost");
			unmount();
			expect(document.title).toBe("Bifrost Integrations");
		});

		it("updates the title when the input changes", () => {
			const { rerender } = renderHook(
				({ title }) => useDocumentChrome({ title, logo: null }),
				{ initialProps: { title: "First | Bifrost" } },
			);
			expect(document.title).toBe("First | Bifrost");
			rerender({ title: "Second | Bifrost" });
			expect(document.title).toBe("Second | Bifrost");
		});

		it("leaves the title untouched when title is falsy", () => {
			renderHook(() =>
				useDocumentChrome({ title: undefined, logo: null }),
			);
			expect(document.title).toBe("Bifrost Integrations");
		});
	});

	describe("favicon", () => {
		it("sets the favicon href from a logo data URL and restores on unmount", () => {
			const { unmount } = renderHook(() =>
				useDocumentChrome({ title: null, logo: SVG_DATA_URL }),
			);
			expect(faviconLink().getAttribute("href")).toBe(SVG_DATA_URL);
			unmount();
			expect(faviconLink().getAttribute("href")).toBe("/logo.svg");
		});

		it("drops the hardcoded type so a non-SVG logo is not mislabeled, and restores it", () => {
			const { unmount } = renderHook(() =>
				useDocumentChrome({ title: null, logo: PNG_DATA_URL }),
			);
			expect(faviconLink().getAttribute("href")).toBe(PNG_DATA_URL);
			expect(faviconLink().getAttribute("type")).toBeNull();
			unmount();
			expect(faviconLink().getAttribute("type")).toBe("image/svg+xml");
			expect(faviconLink().getAttribute("href")).toBe("/logo.svg");
		});

		it("leaves the favicon untouched when logo is falsy", () => {
			renderHook(() =>
				useDocumentChrome({ title: "X | Bifrost", logo: null }),
			);
			expect(faviconLink().getAttribute("href")).toBe("/logo.svg");
			expect(faviconLink().getAttribute("type")).toBe("image/svg+xml");
		});
	});

	describe("platform branding", () => {
		it("uses a previously saved square logo without an upload", () => {
			setPlatformFavicon("/api/branding/logo/square");
			expect(new URL(faviconLink().href).pathname).toBe(
				"/api/branding/logo/square",
			);
			expect(faviconLink().getAttribute("type")).toBeNull();
		});

		it("refreshes cached logo bytes and resets to the shipped icon", () => {
			vi.spyOn(Date, "now")
				.mockReturnValueOnce(100)
				.mockReturnValueOnce(200);
			setPlatformFavicon("/api/branding/logo/square");
			const first = faviconLink().href;
			setPlatformFavicon("/api/branding/logo/square");
			expect(faviconLink().href).not.toBe(first);
			setPlatformFavicon(null);
			expect(faviconLink().getAttribute("href")).toBe("/logo.svg");
			expect(faviconLink().type).toBe("image/svg+xml");
			vi.restoreAllMocks();
		});

		it("keeps the app override during branding refresh and restores the latest platform icon", () => {
			setPlatformFavicon("/api/branding/logo/square");
			const { unmount } = renderHook(() =>
				useDocumentChrome({ title: null, logo: PNG_DATA_URL }),
			);
			setPlatformFavicon("/api/branding/logo/square?replacement=1");
			expect(faviconLink().getAttribute("href")).toBe(PNG_DATA_URL);
			unmount();
			expect(
				new URL(faviconLink().href).searchParams.get("replacement"),
			).toBe("1");
		});

		it("restores the default after branding is reset while an app is open", () => {
			setPlatformFavicon("/api/branding/logo/square");
			const { unmount } = renderHook(() =>
				useDocumentChrome({ title: null, logo: SVG_DATA_URL }),
			);
			setPlatformFavicon(null);
			expect(faviconLink().getAttribute("href")).toBe(SVG_DATA_URL);
			unmount();
			expect(faviconLink().getAttribute("href")).toBe("/logo.svg");
		});
	});

	describe("enabled flag (embed mode)", () => {
		it("is a no-op for both title and favicon when disabled", () => {
			renderHook(() =>
				useDocumentChrome({
					title: "Embedded | Bifrost",
					logo: SVG_DATA_URL,
					enabled: false,
				}),
			);
			expect(document.title).toBe("Bifrost Integrations");
			expect(faviconLink().getAttribute("href")).toBe("/logo.svg");
			expect(faviconLink().getAttribute("type")).toBe("image/svg+xml");
		});
	});
});
