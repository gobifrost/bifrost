import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DateTimePicker } from "./date-time-picker";

describe("DateTimePicker", () => {
	it("renders placeholder when value is null", () => {
		render(<DateTimePicker value={null} onChange={() => {}} />);
		expect(
			screen.getByRole("button", { name: /pick date and time/i }),
		).toHaveTextContent(/pick a date and time/i);
	});

	it("renders formatted datetime when value is set", () => {
		// April 25, 2026 at 9:30 AM local
		const value = new Date(2026, 3, 25, 9, 30, 0, 0);
		render(<DateTimePicker value={value} onChange={() => {}} />);
		const trigger = screen.getByRole("button", {
			name: /pick date and time/i,
		});
		expect(trigger.textContent).toMatch(/2026/);
		expect(trigger.textContent).toMatch(/9:30\s*AM/i);
	});

	it("opens the popover with a calendar when the trigger is clicked", async () => {
		const user = userEvent.setup();
		render(<DateTimePicker value={null} onChange={() => {}} />);
		await user.click(
			screen.getByRole("button", { name: /pick date and time/i }),
		);
		// react-day-picker renders a grid role for the calendar
		expect(await screen.findByRole("grid")).toBeInTheDocument();
	});

	it("preserves the time portion when a new day is selected on the calendar", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		// April 15, 2026 at 14:30 local
		const value = new Date(2026, 3, 15, 14, 30, 0, 0);
		render(<DateTimePicker value={value} onChange={onChange} />);

		await user.click(
			screen.getByRole("button", { name: /pick date and time/i }),
		);

		// Click a different day in the same month — pick April 20
		// react-day-picker exposes day buttons with accessible names like "April 20, 2026"
		const dayButton = await screen.findByRole("button", {
			name: /April 20(st|nd|rd|th)?,?\s*2026/i,
		});
		await user.click(dayButton);

		expect(onChange).toHaveBeenCalledTimes(1);
		const next = onChange.mock.calls[0][0] as Date;
		expect(next).toBeInstanceOf(Date);
		expect(next.getFullYear()).toBe(2026);
		expect(next.getMonth()).toBe(3); // April
		expect(next.getDate()).toBe(20);
		expect(next.getHours()).toBe(14);
		expect(next.getMinutes()).toBe(30);
	});

	it("preserves the date when the time input changes", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		// April 15, 2026 at 14:30 local
		const value = new Date(2026, 3, 15, 14, 30, 0, 0);
		render(<DateTimePicker value={value} onChange={onChange} />);

		await user.click(
			screen.getByRole("button", { name: /pick date and time/i }),
		);

		const timeInput = await screen.findByLabelText(/^time$/i);
		// Set time to 09:15 — fire a synthetic change because <input type="time">
		// character-typing is inconsistent across happy-dom/jsdom vs real browsers.
		fireEvent.change(timeInput, { target: { value: "09:15" } });

		expect(onChange).toHaveBeenCalled();
		const next = onChange.mock.calls[
			onChange.mock.calls.length - 1
		][0] as Date;
		expect(next).toBeInstanceOf(Date);
		// Date portion preserved
		expect(next.getFullYear()).toBe(2026);
		expect(next.getMonth()).toBe(3);
		expect(next.getDate()).toBe(15);
		// Time updated
		expect(next.getHours()).toBe(9);
		expect(next.getMinutes()).toBe(15);
	});

	it("disables the trigger when disabled is true", async () => {
		const user = userEvent.setup();
		render(<DateTimePicker value={null} onChange={() => {}} disabled />);
		const trigger = screen.getByRole("button", {
			name: /pick date and time/i,
		});
		expect(trigger).toBeDisabled();
		await user.click(trigger);
		// Popover should not have opened
		expect(screen.queryByRole("grid")).not.toBeInTheDocument();
	});

	it("closes an open picker when scheduling becomes disabled", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		const { rerender } = render(
			<DateTimePicker value={null} onChange={onChange} />,
		);
		await user.click(
			screen.getByRole("button", { name: /pick date and time/i }),
		);
		expect(await screen.findByRole("grid")).toBeInTheDocument();
		rerender(<DateTimePicker value={null} onChange={onChange} disabled />);
		expect(screen.queryByRole("grid")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /pick date and time/i }),
		).toBeDisabled();
		expect(onChange).not.toHaveBeenCalled();
	});

	it("disables calendar days before minDate", async () => {
		const user = userEvent.setup();
		// minDate = April 10, 2026
		const minDate = new Date(2026, 3, 10, 0, 0, 0, 0);
		// Start the calendar on that month by seeding value to a day within range
		const value = new Date(2026, 3, 15, 12, 0, 0, 0);
		render(
			<DateTimePicker
				value={value}
				onChange={() => {}}
				minDate={minDate}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /pick date and time/i }),
		);

		// April 9, 2026 should be disabled (day before minDate)
		const disallowedDay = await screen.findByRole("button", {
			name: /April 9(st|nd|rd|th)?,?\s*2026/i,
		});
		// react-day-picker marks disabled days with aria-disabled="true" on the
		// day button. If the library ever changes this, weaken the assertion
		// here rather than monkey-patching.
		expect(
			disallowedDay.getAttribute("aria-disabled") === "true" ||
				disallowedDay.hasAttribute("disabled"),
		).toBe(true);
	});
});

it.each(["minimum", "maximum"])(
	"clamps time edits across the %s boundary day",
	async (boundary) => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		const limit = new Date(2026, 3, 15, 12, 0);
		const value = new Date(
			2026,
			3,
			boundary === "minimum" ? 14 : 16,
			10,
			0,
		);
		render(
			<DateTimePicker
				value={value}
				onChange={onChange}
				minDate={boundary === "minimum" ? limit : undefined}
				maxDate={boundary === "maximum" ? limit : undefined}
			/>,
		);
		await user.click(
			screen.getByRole("button", { name: /pick date and time/i }),
		);
		fireEvent.change(await screen.findByLabelText(/^time$/i), {
			target: { value: "09:15" },
		});
		expect(onChange).toHaveBeenCalledWith(limit);
	},
);
