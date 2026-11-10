/**
 * Component tests for ScheduleControls.
 *
 * Covers the "Schedule for later" switch reveal, the quick-pick buttons
 * (15m / 1h / 4h / tomorrow 9 AM), and the past-time validation hint.
 * The DateTimePicker itself is exercised in its own test file; here we just
 * confirm it's rendered in the revealed state.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ScheduleControls } from "./ScheduleControls";

// Fixed "now" so tomorrow-9-AM computations are deterministic.
const FIXED_NOW = new Date("2026-04-23T14:30:00.000-04:00"); // 14:30 local

beforeEach(() => {
	// shouldAdvanceTime: let userEvent's internal setTimeout calls progress
	// so clicks resolve. Without this, fake timers wedge userEvent.
	vi.useFakeTimers({ shouldAdvanceTime: true });
	vi.setSystemTime(FIXED_NOW);
});

afterEach(() => {
	vi.useRealTimers();
});

describe("ScheduleControls — collapsed (run-now) state", () => {
	it("renders only the switch when value is null", () => {
		renderWithProviders(
			<ScheduleControls value={null} onChange={() => {}} />,
		);

		const scheduleSwitch = screen.getByRole("switch", {
			name: /schedule for later/i,
		});
		expect(scheduleSwitch).toBeInTheDocument();
		expect(scheduleSwitch).not.toBeChecked();

		// Quick picks should not be present yet.
		expect(
			screen.queryByRole("button", { name: /in 15 min/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /tomorrow 9 am/i }),
		).not.toBeInTheDocument();
	});

	it("calls onChange({ delay_seconds: 900 }) when the switch is first checked", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ScheduleControls value={null} onChange={onChange} />,
		);

		await user.click(
			screen.getByRole("switch", { name: /schedule for later/i }),
		);

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith({ delay_seconds: 900 });
	});
});

describe("ScheduleControls — expanded (run-later) state", () => {
	it("reveals quick picks and the picker when value is non-null", () => {
		renderWithProviders(
			<ScheduleControls
				value={{ delay_seconds: 900 }}
				onChange={() => {}}
			/>,
		);

		expect(
			screen.getByRole("button", { name: /in 15 min/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /in 1 hour/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /in 4 hours/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /tomorrow 9 am/i }),
		).toBeInTheDocument();
		// The date-time picker trigger is rendered below the quick picks.
		expect(
			screen.getByRole("button", { name: /pick date and time/i }),
		).toBeInTheDocument();
	});

	it("emits { delay_seconds: 900 } for In 15 min", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ScheduleControls
				value={{ delay_seconds: 3600 }}
				onChange={onChange}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /in 15 min/i }));
		expect(onChange).toHaveBeenLastCalledWith({ delay_seconds: 900 });
	});

	it("emits { delay_seconds: 3600 } for In 1 hour", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ScheduleControls
				value={{ delay_seconds: 900 }}
				onChange={onChange}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /in 1 hour/i }));
		expect(onChange).toHaveBeenLastCalledWith({ delay_seconds: 3600 });
	});

	it("emits a scheduled_at at tomorrow 9 AM local for Tomorrow 9 AM", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ScheduleControls
				value={{ delay_seconds: 900 }}
				onChange={onChange}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /tomorrow 9 am/i }),
		);

		expect(onChange).toHaveBeenCalledTimes(1);
		const arg = onChange.mock.calls[0][0] as { scheduled_at?: string };
		expect(arg.scheduled_at).toBeDefined();

		const emitted = new Date(arg.scheduled_at!);
		// Tomorrow relative to FIXED_NOW in local time should be April 24, 2026 at 09:00 local.
		expect(emitted.getHours()).toBe(9);
		expect(emitted.getMinutes()).toBe(0);
		expect(emitted.getDate()).toBe(FIXED_NOW.getDate() + 1);
		expect(emitted.getMonth()).toBe(FIXED_NOW.getMonth());
		expect(emitted.getFullYear()).toBe(FIXED_NOW.getFullYear());
	});

	it("highlights the active quick pick via aria-pressed", () => {
		renderWithProviders(
			<ScheduleControls
				value={{ delay_seconds: 3600 }}
				onChange={() => {}}
			/>,
		);

		const hourBtn = screen.getByRole("button", { name: /in 1 hour/i });
		const fifteenBtn = screen.getByRole("button", { name: /in 15 min/i });
		expect(hourBtn).toHaveAttribute("aria-pressed", "true");
		expect(fifteenBtn).toHaveAttribute("aria-pressed", "false");
	});
});

describe("ScheduleControls — unchecking", () => {
	it("calls onChange(null) when the switch is unchecked", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ScheduleControls
				value={{ delay_seconds: 900 }}
				onChange={onChange}
			/>,
		);

		await user.click(
			screen.getByRole("switch", { name: /schedule for later/i }),
		);
		expect(onChange).toHaveBeenCalledWith(null);
	});
});

describe("ScheduleControls — validation", () => {
	it("shows a past-time hint when scheduled_at is in the past", () => {
		const past = new Date(FIXED_NOW.getTime() - 60_000).toISOString();
		renderWithProviders(
			<ScheduleControls
				value={{ scheduled_at: past }}
				onChange={() => {}}
			/>,
		);

		expect(screen.getByText(/past/i)).toBeInTheDocument();
	});

	it("does not show the past hint for a future scheduled_at", () => {
		const future = new Date(
			FIXED_NOW.getTime() + 60 * 60_000,
		).toISOString();
		renderWithProviders(
			<ScheduleControls
				value={{ scheduled_at: future }}
				onChange={() => {}}
			/>,
		);

		expect(screen.queryByText(/past/i)).not.toBeInTheDocument();
	});
});

describe("ScheduleControls — disabled", () => {
	it("disables the switch when disabled=true", () => {
		renderWithProviders(
			<ScheduleControls value={null} onChange={() => {}} disabled />,
		);

		const scheduleSwitch = screen.getByRole("switch", {
			name: /schedule for later/i,
		});
		expect(scheduleSwitch).toBeDisabled();
	});

	it("disables quick picks and the picker when expanded + disabled", () => {
		renderWithProviders(
			<ScheduleControls
				value={{ delay_seconds: 900 }}
				onChange={() => {}}
				disabled
			/>,
		);

		expect(
			screen.getByRole("button", { name: /in 15 min/i }),
		).toBeDisabled();
		expect(
			screen.getByRole("button", { name: /tomorrow 9 am/i }),
		).toBeDisabled();
		expect(
			screen.getByRole("button", { name: /pick date and time/i }),
		).toBeDisabled();
	});
});

it.each(["not-a-date", "2026-02-30T12:00:00Z"])(
	"ignores invalid saved schedule %s",
	(scheduled_at) => {
		renderWithProviders(
			<ScheduleControls value={{ scheduled_at }} onChange={() => {}} />,
		);
		expect(
			screen.getByRole("button", { name: /pick date and time/i }),
		).toHaveTextContent("Pick a date and time");
		expect(screen.queryByText(/past/i)).not.toBeInTheDocument();
	},
);
