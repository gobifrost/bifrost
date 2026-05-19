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
