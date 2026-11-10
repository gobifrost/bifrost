import * as React from "react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { DateTimePicker } from "@/components/ui/date-time-picker";
import { Switch } from "@/components/ui/switch";

export interface Schedule {
	/** ISO-8601 UTC string, set when the user picks an absolute time. */
	scheduled_at?: string;
	/** Positive int seconds from submit; set for relative quick picks. */
	delay_seconds?: number;
}

export interface ScheduleControlsProps {
	/** null = "run now"; non-null = "run later" with either delay_seconds or scheduled_at set. */
	value: Schedule | null;
	onChange: (next: Schedule | null) => void;
	disabled?: boolean;
}

const DEFAULT_DELAY_SECONDS = 900; // 15 min — reasonable default on first check.
const MAX_DELAY_SECONDS = 365 * 24 * 60 * 60; // 1 year, matches backend.

function computeTomorrow9Am(now: Date): Date {
	const next = new Date(now);
	next.setDate(next.getDate() + 1);
	next.setHours(9, 0, 0, 0);
	return next;
}

function pickToIso(date: Date): string {
	return date.toISOString();
}

function matchesDelay(
	value: Schedule | null,
	seconds: number,
	toleranceSec = 30,
): boolean {
	if (!value || value.delay_seconds === undefined) return false;
	return Math.abs(value.delay_seconds - seconds) <= toleranceSec;
}

function parseScheduledAt(value?: string): Date | null {
	if (!value) return null;
	const date = new Date(value);
	if (!Number.isFinite(date.getTime())) return null;
	// JavaScript normalizes impossible ISO dates, such as February 30.
	const parts = /^(\d{4})-(\d{2})-(\d{2})(?:T|$)/.exec(value);
	if (parts) {
		const [, year, month, day] = parts.map(Number);
		const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
		if (month < 1 || month > 12 || day < 1 || day > daysInMonth)
			return null;
	}
	return date;
}

function matchesTomorrow9Am(value: Schedule | null, now: Date): boolean {
	return (
		parseScheduledAt(value?.scheduled_at)?.getTime() ===
		computeTomorrow9Am(now).getTime()
	);
}

interface QuickPick {
	label: string;
	/** Called with the live "now" so Tomorrow 9 AM is computed at click time. */
	toSchedule: (now: Date) => Schedule;
	isActive: (value: Schedule | null, now: Date) => boolean;
}

const QUICK_PICKS: QuickPick[] = [
	{
		label: "In 15 min",
		toSchedule: () => ({ delay_seconds: 900 }),
		isActive: (v) => matchesDelay(v, 900),
	},
	{
		label: "In 1 hour",
		toSchedule: () => ({ delay_seconds: 3600 }),
		isActive: (v) => matchesDelay(v, 3600),
	},
	{
		label: "In 4 hours",
		toSchedule: () => ({ delay_seconds: 4 * 3600 }),
		isActive: (v) => matchesDelay(v, 4 * 3600),
	},
	{
		label: "Tomorrow 9 AM",
		toSchedule: (now) => ({
			scheduled_at: pickToIso(computeTomorrow9Am(now)),
		}),
		isActive: (v, now) => matchesTomorrow9Am(v, now),
	},
];

export function ScheduleControls({
	value,
	onChange,
	disabled,
}: ScheduleControlsProps): React.JSX.Element {
	const checked = value !== null;
	// Snapshot "now" once per render so quick-pick activity checks are stable
	// within a paint, but we use a fresh Date() when the user actually clicks.
	const now = new Date();

	const handleCheckedChange = (next: boolean | "indeterminate") => {
		if (next === true) {
			onChange({ delay_seconds: DEFAULT_DELAY_SECONDS });
		} else {
			onChange(null);
		}
	};

	const handleQuickPick = (pick: QuickPick) => {
		onChange(pick.toSchedule(new Date()));
	};

	const handlePickerChange = (date: Date | null) => {
		if (!date) return;
		onChange({ scheduled_at: pickToIso(date) });
	};

	const pickerValue = parseScheduledAt(value?.scheduled_at);

	const minDate = new Date(now.getTime() + 60_000);
	const maxDate = new Date(now.getTime() + MAX_DELAY_SECONDS * 1000);

	const scheduledInPast =
		pickerValue !== null && pickerValue.getTime() < now.getTime();

	return (
		<div className="flex flex-col gap-3">
			<Label className="flex min-h-11 items-center gap-2">
				<Switch
					checked={checked}
					onCheckedChange={handleCheckedChange}
					disabled={disabled}
					aria-label="Schedule for later"
				/>
				<span>Schedule for later</span>
			</Label>

			{checked && (
				<div className="flex min-w-0 flex-col gap-3 border-l border-border pl-3 sm:pl-6">
					<div className="flex flex-wrap gap-2">
						{QUICK_PICKS.map((pick) => {
							const active = pick.isActive(value, now);
							return (
								<Button
									key={pick.label}
									type="button"
									size="sm"
									className="min-h-11 sm:min-h-8"
									variant={active ? "default" : "outline"}
									aria-pressed={active}
									disabled={disabled}
									onClick={() => handleQuickPick(pick)}
								>
									{pick.label}
								</Button>
							);
						})}
					</div>

					<div className="flex flex-col gap-1.5">
						<span className="text-sm text-muted-foreground">
							Or pick a specific time:
						</span>
						<DateTimePicker
							value={pickerValue}
							onChange={handlePickerChange}
							minDate={minDate}
							maxDate={maxDate}
							disabled={disabled}
						/>
					</div>

					{scheduledInPast && (
						<p
							className={cn("text-sm text-destructive", "mt-0.5")}
							role="alert"
						>
							Time is in the past
						</p>
					)}
				</div>
			)}
		</div>
	);
}
