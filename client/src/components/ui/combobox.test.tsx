import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import { Combobox } from "./combobox";

describe("Combobox", () => {
	it("filters options by literal label and value text", async () => {
		const { user } = renderWithProviders(
			<Combobox
				options={[
					{ value: "google/nano-banana", label: "Nano Banana" },
					{
						value: "deepseek/deepseek-v4-pro",
						label: "DeepSeek V4 Pro",
					},
					{ value: "openai/sora", label: "Sora" },
				]}
				onValueChange={vi.fn()}
				placeholder="Choose model"
				searchPlaceholder="Search models..."
			/>,
		);

		await user.click(screen.getByRole("combobox"));
		await user.type(
			screen.getByPlaceholderText("Search models..."),
			"banana",
		);

		expect(
			screen.getByRole("option", { name: "Nano Banana" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("option", { name: "DeepSeek V4 Pro" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("option", { name: "Sora" }),
		).not.toBeInTheDocument();
	});
});
