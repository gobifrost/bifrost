import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Combobox } from "./combobox";

const OPTIONS = [
	{
		value: "role_based",
		label: "Role-based",
		description: "Only people with an assigned role",
	},
	{
		value: "authenticated",
		label: "Everyone except external users",
		description: "Any signed-in internal user",
	},
];

describe("Combobox", () => {
	it("keeps placeholder and selected labels left-aligned inside a full-width trigger", () => {
		const { rerender } = render(
			<Combobox
				aria-label="Access level"
				options={OPTIONS}
				placeholder="Select access level"
			/>,
		);
		const trigger = screen.getByRole("combobox", { name: "Access level" });
		expect(trigger).toHaveClass("w-full", "min-w-0", "text-left");
		expect(screen.getByText("Select access level")).toHaveClass(
			"flex-1",
			"text-left",
		);

		rerender(
			<Combobox
				aria-label="Access level"
				options={OPTIONS}
				value="authenticated"
			/>,
		);
		expect(screen.getByText("Everyone except external users")).toHaveClass(
			"flex-1",
			"text-left",
		);
	});

	it("selects a described option without clearing an already-selected value", () => {
		const onValueChange = vi.fn();
		render(
			<Combobox
				aria-label="Access level"
				options={OPTIONS}
				value="role_based"
				onValueChange={onValueChange}
			/>,
		);
		fireEvent.click(screen.getByRole("combobox", { name: "Access level" }));
		fireEvent.click(screen.getByText("Everyone except external users"));
		expect(onValueChange).toHaveBeenCalledWith("authenticated");
	});
});
