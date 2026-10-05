import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createBrandPalette } from "./brand-palette";
import { BifrostProvider, useBifrostContext } from "./provider";
import { useBranding } from "./use-branding";

const BRANDING = {
	application_name: "Acme Desk",
	square_logo_url: "/api/branding/logo/square.png",
	rectangle_logo_url: "/api/branding/logo/rectangle.png",
	primary_color: "#3366ff",
};

afterEach(() => {
	document.querySelector("style#bifrost-branding-theme")?.remove();
	document.documentElement.style.removeProperty("--logo-square-url");
	document.documentElement.style.removeProperty("--logo-rectangle-url");
});

function Branding({ applyTheme = true }: { applyTheme?: boolean }) {
	const { data, loading, error, squareLogoUrl, rectangleLogoUrl, applicationName, palette, colors, refetch } =
		useBranding({ applyTheme });
	return (
		<>
			<output aria-label="branding">
				{loading
					? "loading"
					: error
						? `error:${error.message}`
						: `${data?.square_logo_url}|${squareLogoUrl}|${rectangleLogoUrl}|${applicationName}|${palette.isCustom}|${colors.primary}`}
			</output>
			<button onClick={() => void refetch()}>refresh</button>
		</>
	);
}

describe("useBranding", () => {
	it("loads branding, resolves root-relative logos to the API origin, and exposes the shared palette", async () => {
		const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
			expect(String(input)).toBe("https://api.example/api/branding");
			return new Response(JSON.stringify(BRANDING), { status: 200 });
		});
		render(
			<BifrostProvider baseUrl="https://api.example/" token="token" fetchImpl={fetchImpl}>
				<Branding applyTheme={false} />
			</BifrostProvider>,
		);

		const palette = createBrandPalette(BRANDING.primary_color);
		await waitFor(() =>
			expect(screen.getByRole("status", { name: "branding" })).toHaveTextContent(
				`https://api.example${BRANDING.square_logo_url}|https://api.example${BRANDING.square_logo_url}|https://api.example${BRANDING.rectangle_logo_url}|Acme Desk|true|${palette.light.primary}`,
			),
		);
	});

	it("keeps logo paths root-relative for a same-origin provider", async () => {
		const fetchImpl = vi.fn(async () => new Response(JSON.stringify(BRANDING)));
		render(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Branding applyTheme={false} />
			</BifrostProvider>,
		);
		await waitFor(() =>
			expect(screen.getByRole("status", { name: "branding" })).toHaveTextContent(
				`${BRANDING.square_logo_url}|${BRANDING.square_logo_url}|${BRANDING.rectangle_logo_url}`,
			),
		);
	});

	it("applies theme by default and does not apply it when opted out", async () => {
		const fetchImpl = vi.fn(async () => new Response(JSON.stringify(BRANDING)));
		const { unmount } = render(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Branding />
			</BifrostProvider>,
		);
		await screen.findByText(/Acme Desk/);
		expect(document.querySelector("style#bifrost-branding-theme")).not.toBeNull();
		unmount();
		document.querySelector("style#bifrost-branding-theme")?.remove();

		render(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Branding applyTheme={false} />
			</BifrostProvider>,
		);
		await screen.findByText(/Acme Desk/);
		expect(document.querySelector("style#bifrost-branding-theme")).toBeNull();
	});

	it("uses the default palette when branding has no color or logos", async () => {
		function Defaults() {
			const { data, squareLogoUrl, rectangleLogoUrl, palette } = useBranding({
				applyTheme: false,
			});
			return (
				<output aria-label="defaults">
					{data ? "loaded" : "empty"}|{squareLogoUrl ?? "none"}|
					{rectangleLogoUrl ?? "none"}|{String(palette.isCustom)}
				</output>
			);
		}
		render(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={vi.fn(async () => new Response("{}"))}>
				<Defaults />
			</BifrostProvider>,
		);
		await screen.findByText("loaded|none|none|false");
	});

	it("removes logo CSS variables when a refetch returns no logos", async () => {
		let calls = 0;
		const fetchImpl = vi.fn(async () => {
			calls += 1;
			return new Response(
				JSON.stringify(calls === 1 ? BRANDING : { primary_color: "#3366ff" }),
			);
		});
		render(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Branding />
			</BifrostProvider>,
		);
		await screen.findByText(/Acme Desk/);
		expect(document.documentElement.style.getPropertyValue("--logo-square-url")).toContain(
			BRANDING.square_logo_url,
		);
		await act(async () => screen.getByRole("button", { name: "refresh" }).click());
		await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
		await waitFor(() =>
			expect(document.documentElement.style.getPropertyValue("--logo-square-url")).toBe(""),
		);
		expect(document.documentElement.style.getPropertyValue("--logo-rectangle-url")).toBe("");
	});

	it("exposes a failed branding request without applying a default theme", async () => {
		render(
			<BifrostProvider
				baseUrl="/"
				token="token"
				fetchImpl={vi.fn(async () => new Response(JSON.stringify({ detail: "forbidden" }), { status: 403 }))}
			>
				<Branding />
			</BifrostProvider>,
		);
		await waitFor(() =>
			expect(screen.getByRole("status", { name: "branding" })).toHaveTextContent(
				"error:403 Request failed: forbidden",
			),
		);
		expect(document.querySelector("style#bifrost-branding-theme")).toBeNull();
	});

	it("selects the shared dark palette without refetching branding", async () => {
		function ThemeProbe() {
			const { setTheme } = useBifrostContext();
			return <button onClick={() => setTheme("dark")}>dark</button>;
		}
		const fetchImpl = vi.fn(async () => new Response(JSON.stringify(BRANDING)));
		render(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Branding applyTheme={false} />
				<ThemeProbe />
			</BifrostProvider>,
		);
		await screen.findByText(/Acme Desk/);
		await act(async () => screen.getByRole("button", { name: "dark" }).click());
		expect(screen.getByRole("status", { name: "branding" })).toHaveTextContent(
			createBrandPalette(BRANDING.primary_color).dark.primary,
		);
		expect(fetchImpl).toHaveBeenCalledTimes(1);
	});
});
