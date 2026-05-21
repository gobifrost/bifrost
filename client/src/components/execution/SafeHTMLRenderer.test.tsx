/**
 * Component tests for SafeHTMLRenderer.
 *
 * Critical behaviours: HTML content ends up in the DOM; DOMPurify strips
 * executable tags and dangerous event handlers; the "Open" button delegates
 * to window.open with sanitized HTML.
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

	it("strips executable tags and inline event handlers", () => {
		const { container } = renderWithProviders(
			<SafeHTMLRenderer html='<div onclick="alert(1)"><img src="x" onerror="alert(2)" /><script>alert(3)</script><p onload="alert(4)">safe text</p></div>' />,
		);

		expect(container.querySelector("script")).toBeNull();
		expect(container.querySelector("[onclick]")).toBeNull();
		expect(container.querySelector("[onerror]")).toBeNull();
		expect(container.querySelector("[onload]")).toBeNull();
		expect(container.textContent).toContain("safe text");
	});

	it("strips javascript URLs from links", () => {
		const { container } = renderWithProviders(
			<SafeHTMLRenderer html='<a href="javascript:alert(1)">link</a>' />,
		);

		const link = container.querySelector("a");
		expect(link).not.toBeNull();
		expect(link?.getAttribute("href")).toBeNull();
		expect(link?.textContent).toBe("link");
	});

	it("extracts body content when passed a full HTML document", () => {
		const html =
			"<!DOCTYPE html><html><head><title>T</title></head><body><h1>Hi</h1></body></html>";
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

	it("writes sanitized HTML to the new window", async () => {
		const write = vi.fn();
		openSpy.mockReturnValue({
			document: {
				write,
				close: vi.fn(),
			},
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
		} as any);

		const { user } = renderWithProviders(
			<SafeHTMLRenderer html='<script>alert(1)</script><p onclick="alert(2)">hi</p>' />,
		);

		await user.click(screen.getByRole("button", { name: /open/i }));
		expect(write).toHaveBeenCalledOnce();
		const writtenHTML = write.mock.calls[0]?.[0] as string;
		expect(writtenHTML).not.toContain("<script");
		expect(writtenHTML).not.toContain("onclick");
		expect(writtenHTML).toContain("<p>hi</p>");
	});
});
