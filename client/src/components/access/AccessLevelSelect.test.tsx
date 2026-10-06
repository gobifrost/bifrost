import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import { AccessLevelSelect, accessLevelOptions } from "./AccessLevelSelect";

describe("AccessLevelSelect", () => {
	it("preserves the current value when the selected option is reselected", async () => {
		const handleChange = vi.fn();
		const { user } = renderWithProviders(
			<AccessLevelSelect
				value="role_based"
				onValueChange={handleChange}
			/>,
		);

		await user.click(
			screen.getByRole("combobox", { name: "Access level" }),
		);
		await user.click(screen.getByRole("option", { name: /Role-based/ }));

		expect(handleChange).toHaveBeenCalledWith("role_based");
	});

	it("uses the configured No change value for bulk access updates", async () => {
		const handleChange = vi.fn();
		const { user } = renderWithProviders(
			<AccessLevelSelect
				value="__keep__"
				onValueChange={handleChange}
				includeNoChange
				noChangeValue="__keep__"
			/>,
		);

		expect(
			screen.getByRole("combobox", { name: "Access level" }),
		).toHaveTextContent("No change");
		await user.click(
			screen.getByRole("combobox", { name: "Access level" }),
		);
		await user.click(screen.getByRole("option", { name: /No change/ }));

		expect(handleChange).toHaveBeenCalledWith("__keep__");
	});

	it("renders selected option icons and honors disabled state", () => {
		renderWithProviders(
			<AccessLevelSelect
				value="authenticated"
				onValueChange={vi.fn()}
				disabled
			/>,
		);

		const trigger = screen.getByRole("combobox", { name: "Access level" });
		expect(trigger).toBeDisabled();
		expect(trigger).toHaveTextContent("Everyone except external users");
		expect(trigger.querySelector("svg")).toBeTruthy();
	});

	it("exposes only the three shared access levels by default", () => {
		expect(accessLevelOptions().map((option) => option.value)).toEqual([
			"role_based",
			"authenticated",
			"everyone",
		]);
	});
});
