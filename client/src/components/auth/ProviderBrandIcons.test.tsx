import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { GoogleBrandIcon, MicrosoftBrandIcon } from "./ProviderBrandIcons";

describe("ProviderBrandIcons", () => {
	it("renders the Microsoft mark with exact brand fills", () => {
		const { container } = render(<MicrosoftBrandIcon />);

		const svg = container.querySelector("svg");
		expect(svg).not.toBeNull();
		const fills = [...container.querySelectorAll("rect")].map((el) =>
			el.getAttribute("fill"),
		);
		expect(fills).toEqual(["#F25022", "#7FBA00", "#00A4EF", "#FFB900"]);
	});

	it("renders the Google mark with exact brand fills", () => {
		const { container } = render(<GoogleBrandIcon />);

		const svg = container.querySelector("svg");
		expect(svg).not.toBeNull();
		const fills = [...container.querySelectorAll("path")].map((el) =>
			el.getAttribute("fill"),
		);
		expect(fills).toEqual(["#4285F4", "#34A853", "#FBBC05", "#EA4335"]);
	});

	it("accepts a custom className", () => {
		const { container } = render(<MicrosoftBrandIcon className="h-4 w-4" />);

		expect(container.querySelector("svg")).toHaveClass("h-4 w-4");
	});
});
