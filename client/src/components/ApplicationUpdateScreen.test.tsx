import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { requestApplicationReload } from "@/lib/application-update";
import {
	ApplicationUpdateGate,
	ApplicationUpdateScreen,
} from "./ApplicationUpdateScreen";

describe("ApplicationUpdateScreen", () => {
	afterEach(() => {
		vi.restoreAllMocks();
		sessionStorage.clear();
	});

	it("explains that fresh application assets are loading", () => {
		render(<ApplicationUpdateScreen />);

		expect(
			screen.getByRole("heading", { name: "Application updated" }),
		).toBeInTheDocument();
		expect(screen.getByText("Loading the latest version…")).toBeInTheDocument();
		expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
	});

	it("replaces the main application before a deployment reload", () => {
		vi.spyOn(window, "setTimeout").mockImplementation(() => 1);
		render(
			<ApplicationUpdateGate>
				<div>Current application</div>
			</ApplicationUpdateGate>,
		);

		expect(screen.getByText("Current application")).toBeInTheDocument();
		act(() => {
			requestApplicationReload("component-update", 5_000);
		});

		expect(
			screen.getByRole("heading", { name: "Application updated" }),
		).toBeInTheDocument();
		expect(screen.queryByText("Current application")).not.toBeInTheDocument();
	});
});
