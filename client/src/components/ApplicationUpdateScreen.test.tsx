import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { requestApplicationReload } from "@/lib/application-update";
import {
	ApplicationUpdateGate,
	ApplicationUpdateScreen,
} from "./ApplicationUpdateScreen";

describe("ApplicationUpdateScreen", () => {
	afterEach(() => {
		vi.useRealTimers();
		vi.restoreAllMocks();
		sessionStorage.clear();
		document.documentElement.style.removeProperty("--logo-square-url");
	});

	it("explains that fresh application assets are loading", () => {
		render(<ApplicationUpdateScreen />);

		expect(
			screen.getByRole("heading", { name: "Application updated" }),
		).toBeInTheDocument();
		expect(screen.getByText("Loading the latest version…")).toBeInTheDocument();
		expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
		expect(screen.getByRole("img", { name: "Application logo" })).toHaveAttribute(
			"src",
			"/logo.svg",
		);
	});

	it("uses applied branding with the default logo as an error fallback", () => {
		document.documentElement.style.setProperty(
			"--logo-square-url",
			'url("https://example.test/custom-logo.png")',
		);
		render(<ApplicationUpdateScreen />);

		const logo = screen.getByRole("img", { name: "Application logo" });
		expect(logo).toHaveAttribute(
			"src",
			"https://example.test/custom-logo.png",
		);

		fireEvent.error(logo);
		expect(logo).toHaveAttribute("src", "/logo.svg");
	});

	it("replaces the main application before a deployment reload", () => {
		vi.useFakeTimers();
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
