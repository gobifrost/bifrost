import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { MultiCombobox } from "./multi-combobox";

const options = [
	{ value: "a", label: "Auditors" },
	{ value: "h", label: "Helpdesk" },
];

describe("MultiCombobox", () => {
	it("shows the selected options as removable chips", () => {
		render(
			<MultiCombobox
				options={options}
				value={["a"]}
				onValueChange={vi.fn()}
			/>,
		);

		expect(
			screen.getByRole("list", { name: "Selected options" }),
		).toHaveTextContent("Auditors");
		expect(screen.getByRole("combobox")).toHaveTextContent("1 selected");
	});

	it("leaves the chips out where the page lists the selection itself", () => {
		render(
			<MultiCombobox
				options={options}
				value={["a"]}
				onValueChange={vi.fn()}
				showSelected={false}
			/>,
		);

		expect(
			screen.queryByRole("list", { name: "Selected options" }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("combobox")).toHaveTextContent("1 selected");
	});
});
