import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DatePicker } from "./date-picker";

function ControlledPicker() {
	const [value, setValue] = useState("2026-11-10");
	return (
		<DatePicker aria-label="Start Date" value={value} onChange={setValue} />
	);
}

describe("DatePicker", () => {
	it("selects and clears calendar-date strings using the shared calendar", async () => {
		const user = userEvent.setup();
		render(<ControlledPicker />);
		const trigger = screen.getByRole("button", { name: "Start Date" });
		expect(trigger).toHaveTextContent("Nov 10, 2026");
		expect(trigger).toHaveAccessibleDescription("Nov 10, 2026");
		await user.click(trigger);
		expect(screen.getByRole("grid")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: /November 20.*2026/i }),
		);
		expect(trigger).toHaveTextContent("Nov 20, 2026");
		expect(screen.queryByRole("grid")).not.toBeInTheDocument();
		await user.click(trigger);
		await user.click(screen.getByRole("button", { name: "Clear Date" }));
		expect(trigger).toHaveTextContent("Pick a Date");
		expect(screen.queryByRole("grid")).not.toBeInTheDocument();
	});

	it("keeps the date when choosing the selected day again", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		render(
			<DatePicker
				aria-label="Start Date"
				value="2026-11-10"
				onChange={onChange}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Start Date" }));
		await user.click(
			screen.getByRole("button", { name: /November 10.*2026/i }),
		);
		expect(onChange).toHaveBeenCalledWith("2026-11-10");
		expect(screen.queryByRole("grid")).not.toBeInTheDocument();
	});

	it("supports keyboard day selection and returns focus to the trigger", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		render(
			<DatePicker
				aria-label="Start Date"
				value="2026-11-10"
				onChange={onChange}
			/>,
		);
		const trigger = screen.getByRole("button", { name: "Start Date" });
		trigger.focus();
		await user.keyboard("{Enter}{ArrowRight}{Enter}");
		expect(onChange).toHaveBeenCalledWith("2026-11-11");
		expect(trigger).toHaveFocus();
	});

	it("dismisses with Escape without changing the date", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		render(
			<DatePicker
				aria-label="Start Date"
				value="2026-11-10"
				onChange={onChange}
			/>,
		);
		const trigger = screen.getByRole("button", { name: "Start Date" });
		await user.click(trigger);
		await user.keyboard("{Escape}");
		expect(onChange).not.toHaveBeenCalled();
		expect(screen.queryByRole("grid")).not.toBeInTheDocument();
		expect(trigger).toHaveFocus();
	});

	it("shows the field placeholder and disables clearing an empty date", async () => {
		const user = userEvent.setup();
		render(
			<DatePicker
				aria-label="Start Date"
				value=""
				placeholder="Choose a Start Date"
				onChange={() => {}}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Start Date" }));
		expect(
			screen.getByRole("button", { name: "Start Date" }),
		).toHaveTextContent("Choose a Start Date");
		expect(
			screen.getByRole("button", { name: "Clear Date" }),
		).toBeDisabled();
	});

	it("closes an open calendar when disabled", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		const { rerender } = render(
			<DatePicker
				aria-label="Start Date"
				value="2026-11-10"
				onChange={onChange}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Start Date" }));
		rerender(
			<DatePicker
				aria-label="Start Date"
				value="2026-11-10"
				onChange={onChange}
				disabled
			/>,
		);
		expect(screen.queryByRole("grid")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Start Date" }),
		).toBeDisabled();
		expect(onChange).not.toHaveBeenCalled();
	});
});
