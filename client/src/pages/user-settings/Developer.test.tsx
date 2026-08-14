import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeveloperSettings } from "./Developer";

describe("DeveloperSettings", () => {
	it("advertises the uv-compatible CLI install and download URLs", () => {
		render(<DeveloperSettings />);

		expect(
			screen.getByText(
				/\/api\/cli\/download\/bifrost-cli\.tar\.gz$/,
			),
		).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Download SDK" })).toHaveAttribute(
			"href",
			"/api/cli/download/bifrost-cli.tar.gz",
		);
	});
});
