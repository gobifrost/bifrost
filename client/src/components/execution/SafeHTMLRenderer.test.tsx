/**
 * @vitest-environment jsdom
 *
 * Component tests for SafeHTMLRenderer.
 *
 * Critical behaviours: HTML content ends up in the DOM; DOMPurify strips
 * dangerous event handlers we explicitly FORBID; the "Open" button delegates
 * to window.open.
 *
 * NOTE: this file pins the jsdom environment instead of the suite-wide
 * happy-dom. SafeHTMLRenderer sanitises with DOMPurify in `WHOLE_DOCUMENT`
 * mode; dompurify >= 3.4.8 hoists nodes via `document.insertBefore`, which
 * happy-dom rejects with "Only one element on document allowed" because its
 * HTMLDocument forbids inserting a second element into a populated document.
 * jsdom (the reference DOM) handles WHOLE_DOCUMENT correctly, so the component
 * — which is correct in real browsers — can be exercised faithfully here.
 * Downgrading dompurify below 3.4.8 is not an option: 3.4.8 carries the
 * GHSA-hpcv-96wg-7vj8 realm-safety / mXSS hardening.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { SafeHTMLRenderer } from "./SafeHTMLRenderer";

describe("SafeHTMLRenderer — rendering", () => {
	it("renders sanitised markup into the DOM", () => {
		const { container } = renderWithProviders(
			<SafeHTMLRenderer html="<p><strong>Hello</strong> world</p>" />,
		);
		expect(container.querySelector("strong")?.textContent).toBe("Hello");
		expect(container.textContent).toContain("world");
	});

	it("strips forbidden inline event handlers (onmouseover)", () => {
		const { container } = renderWithProviders(
			<SafeHTMLRenderer html='<p onmouseover="alert(1)">hover me</p>' />,
		);
		const p = container.querySelector("p");
		expect(p).not.toBeNull();
		expect(p?.getAttribute("onmouseover")).toBeNull();
		expect(p?.textContent).toBe("hover me");
	});

	it("extracts body content when passed a full HTML document", () => {
		const html = "<!DOCTYPE html><html><head><title>T</title></head><body><h1>Hi</h1></body></html>";
		const { container } = renderWithProviders(
			<SafeHTMLRenderer html={html} />,
		);
		expect(container.querySelector("h1")?.textContent).toBe("Hi");
	});
});

describe("SafeHTMLRenderer — open in new window", () => {
	let openSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		openSpy = vi.spyOn(window, "open").mockReturnValue({
			document: {
				write: vi.fn(),
				close: vi.fn(),
			},
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
		} as any);
	});

	afterEach(() => {
		openSpy.mockRestore();
	});

	it("calls window.open when the Open button is clicked", async () => {
		const { user } = renderWithProviders(
			<SafeHTMLRenderer html="<p>hi</p>" />,
		);
		await user.click(screen.getByRole("button", { name: /open/i }));
		expect(openSpy).toHaveBeenCalledWith("", "_blank");
	});
});
