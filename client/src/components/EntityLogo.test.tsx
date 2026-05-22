import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { EntityLogo } from "./EntityLogo";

describe("EntityLogo", () => {
	it("renders an img tag pointing at the entity logo endpoint", () => {
		render(
			<EntityLogo
				entityType="app"
				entityId="11111111-1111-1111-1111-111111111111"
				fallback={<span data-testid="fallback">F</span>}
				size={32}
			/>,
		);
		const img = screen.getByTestId("entity-logo");
		expect(img.getAttribute("src")).toContain(
			"/api/applications/11111111-1111-1111-1111-111111111111/logo",
		);
	});

	it("uses the agents endpoint for entityType=agent", () => {
		render(
			<EntityLogo
				entityType="agent"
				entityId="22222222-2222-2222-2222-222222222222"
				fallback={<span data-testid="fallback">F</span>}
				size={32}
			/>,
		);
		const img = screen.getByTestId("entity-logo");
		expect(img.getAttribute("src")).toContain(
			"/api/agents/22222222-2222-2222-2222-222222222222/logo",
		);
	});

	it("falls back to fallback element when the image errors (no logo set)", () => {
		render(
			<EntityLogo
				entityType="app"
				entityId="11111111-1111-1111-1111-111111111111"
				fallback={<span data-testid="fallback">F</span>}
				size={32}
			/>,
		);
		const img = screen.getByTestId("entity-logo");
		fireEvent.error(img);
		expect(screen.getByTestId("fallback")).toBeInTheDocument();
		expect(screen.queryByTestId("entity-logo")).toBeNull();
	});

	it("renders inline logo (data URL) directly without hitting the per-entity endpoint", () => {
		const dataUrl = "data:image/svg+xml;base64,PHN2Zy8+";
		render(
			<EntityLogo
				entityType="app"
				entityId="11111111-1111-1111-1111-111111111111"
				logo={dataUrl}
				fallback={<span data-testid="fallback">F</span>}
				size={32}
			/>,
		);
		const img = screen.getByTestId("entity-logo");
		expect(img.getAttribute("src")).toBe(dataUrl);
	});

	it("renders fallback without making any request when logo is explicitly null", () => {
		render(
			<EntityLogo
				entityType="agent"
				entityId="22222222-2222-2222-2222-222222222222"
				logo={null}
				fallback={<span data-testid="fallback">F</span>}
				size={32}
			/>,
		);
		expect(screen.getByTestId("fallback")).toBeInTheDocument();
		expect(screen.queryByTestId("entity-logo")).toBeNull();
	});

	it("appends cacheKey to bust browser cache", () => {
		render(
			<EntityLogo
				entityType="app"
				entityId="11111111-1111-1111-1111-111111111111"
				fallback={<span>F</span>}
				size={32}
				cacheKey="v2"
			/>,
		);
		const img = screen.getByTestId("entity-logo");
		expect(img.getAttribute("src")).toContain("v2");
	});
});
