import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GitHubTokenField } from "./GitHubTokenField";

describe("GitHubTokenField", () => {
	it("keeps the token masked while showing the validation explanation", () => {
		const error =
			"GitHub token validation failed (HTTP 403): GitHub rate limit exceeded";
		render(
			<GitHubTokenField
				value="synthetic-token"
				saved={false}
				valid={false}
				error={error}
				pending={false}
				disabled={false}
				onChange={vi.fn()}
				onValidate={vi.fn()}
			/>,
		);
		expect(screen.getByRole("alert")).toHaveTextContent(error);
		expect(
			screen.getByLabelText("GitHub Personal Access Token"),
		).toHaveAttribute("type", "password");
		expect(screen.getByRole("alert")).not.toHaveTextContent(
			"synthetic-token",
		);
	});
});
