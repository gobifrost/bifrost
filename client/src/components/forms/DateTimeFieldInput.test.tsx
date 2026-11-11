import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DateTimeFieldInput } from "./DateTimeFieldInput";

describe("DateTimeFieldInput", () => {
	it("selects a calendar day while retaining the local time without a timezone suffix", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		render(
			<DateTimeFieldInput
				aria-label="Appointment"
				value="2026-11-10T09:00"
				onChange={onChange}
			/>,
		);
		const trigger = screen.getByRole("button", { name: "Appointment" });
		expect(trigger).toHaveTextContent("Nov 10, 2026 · 9:00 AM");
		await user.click(trigger);
		await user.click(
			screen.getByRole("button", { name: /November 20.*2026/i }),
		);
		expect(onChange).toHaveBeenCalledWith("2026-11-20T09:00");
	});

	it("edits the local time without changing the date", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		render(
			<DateTimeFieldInput
				aria-label="Appointment"
				value="2026-11-10T09:00"
				onChange={onChange}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Appointment" }));
		fireEvent.change(screen.getByLabelText("Time"), {
			target: { value: "14:30" },
		});
		expect(onChange).toHaveBeenCalledWith("2026-11-10T14:30");
	});

	it("clears to an empty form value", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		render(
			<DateTimeFieldInput
				aria-label="Appointment"
				value="2026-11-10T09:00"
				onChange={onChange}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Appointment" }));
		await user.click(
			screen.getByRole("button", { name: "Clear Date & Time" }),
		);
		expect(onChange).toHaveBeenCalledWith("");
		expect(screen.queryByRole("grid")).not.toBeInTheDocument();
	});

	it("shows the configured placeholder for an empty datetime", () => {
		render(
			<DateTimeFieldInput
				aria-label="Appointment"
				value=""
				onChange={() => {}}
				placeholder="Choose an Appointment"
			/>,
		);
		expect(
			screen.getByRole("button", { name: "Appointment" }),
		).toHaveTextContent("Choose an Appointment");
	});
});
