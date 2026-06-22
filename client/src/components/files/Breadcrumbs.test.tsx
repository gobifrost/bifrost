import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { Breadcrumbs } from "./Breadcrumbs";

describe("Breadcrumbs", () => {
	it("renders scope + location + segments and navigates", () => {
		const onNavigate = vi.fn();
		render(
			<Breadcrumbs
				scopeLabel="Global"
				location="gallery"
				segments={["team", "q1"]}
				onNavigate={onNavigate}
			/>,
		);
		expect(screen.getByText("Global")).toBeInTheDocument();
		expect(screen.getByText("gallery")).toBeInTheDocument();
		fireEvent.click(screen.getByText("team"));
		expect(onNavigate).toHaveBeenCalledWith(1);
		fireEvent.click(screen.getByText("gallery"));
		expect(onNavigate).toHaveBeenCalledWith(0);
		fireEvent.click(screen.getByText("Global"));
		expect(onNavigate).toHaveBeenCalledWith(-1);
	});

	it("shows only the scope at the shares root", () => {
		render(
			<Breadcrumbs
				scopeLabel="Acme"
				location={null}
				segments={[]}
				onNavigate={vi.fn()}
			/>,
		);
		expect(screen.getByText("Acme")).toBeInTheDocument();
		expect(screen.queryByText("gallery")).not.toBeInTheDocument();
	});
});
