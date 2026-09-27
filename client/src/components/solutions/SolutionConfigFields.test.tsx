import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { asConfigSchemas, ConfigValueFields, nonBlankConfigValues } from "./SolutionConfigFields";

describe("SolutionConfigFields", () => {
	it("uses package defaults as placeholders while leaving blank values unset", () => {
		const configs = asConfigSchemas([
			{ key: "REGION", type: "string", required: true, has_package_default: true },
			{ key: "TOKEN", type: "secret", required: true, requires_input: true },
		]);
		const onChange = vi.fn();
		render(<ConfigValueFields configs={configs} values={{}} onChange={onChange} />);
		const region = screen.getByLabelText(/REGION/);
		expect(region).toHaveAttribute("placeholder", "Package default if left blank");
		expect(region).toHaveValue("");
		expect(screen.getByLabelText(/TOKEN/)).toHaveAttribute("type", "password");
		expect(nonBlankConfigValues({ REGION: "", TOKEN: "entered" })).toEqual({ TOKEN: "entered" });
		fireEvent.change(region, { target: { value: "west" } });
		expect(onChange).toHaveBeenCalledWith("REGION", "west");
	});
});
