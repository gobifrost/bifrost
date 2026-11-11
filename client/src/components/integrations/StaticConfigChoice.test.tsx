import { expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { StaticConfigChoice } from "./StaticConfigChoice";

it("selects and clears a scalar choice", async () => {
	const onChange = vi.fn();
	const { user } = renderWithProviders(
		<StaticConfigChoice
			id="region"
			options={["us", "eu"]}
			optionLabels={{ us: "United States", eu: "Europe" }}
			value="us"
			onChange={onChange}
		/>,
	);
	await user.click(screen.getByRole("combobox"));
	await user.click(screen.getByRole("option", { name: "Europe" }));
	expect(onChange).toHaveBeenLastCalledWith("eu");
	await user.click(screen.getByRole("combobox"));
	await user.click(screen.getByRole("option", { name: "United States" }));
	expect(onChange).toHaveBeenLastCalledWith("");
});

it("preserves an existing value removed from the allowed list until changed", () => {
	renderWithProviders(
		<StaticConfigChoice
			id="region"
			options={["eu"]}
			value="us"
			onChange={vi.fn()}
		/>,
	);
	expect(screen.getByRole("combobox")).toHaveTextContent(
		"us (Current Value)",
	);
});
