import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Users } from "lucide-react";

import { EmptyState } from "./empty-state";

describe("EmptyState", () => {
	it("shows the title, description and action", () => {
		render(
			<EmptyState
				icon={Users}
				title="No one has this role yet."
				description="Give it from a person's access."
				action={<button type="button">Open people</button>}
			/>,
		);

		expect(
			screen.getByText("No one has this role yet."),
		).toBeInTheDocument();
		expect(
			screen.getByText("Give it from a person's access."),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Open people" }),
		).toBeInTheDocument();
	});

	it("shows only the title when nothing else is given", () => {
		render(<EmptyState icon={Users} title="No roles found" />);

		expect(screen.getByText("No roles found")).toBeInTheDocument();
		expect(screen.queryByRole("button")).not.toBeInTheDocument();
	});
});
