/** An event time, formatted like the audit log's timestamps. */
export function formatAuditTime(value: string): string {
	return new Date(value).toLocaleString();
}

/** A UTC calendar day (YYYY-MM-DD), as the archive groups events. */
export function formatAuditDay(day: string): string {
	return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, {
		timeZone: "UTC",
	});
}

/** A count of events: "1 event", "4,200 events". */
export function formatEvents(count: number): string {
	return `${count.toLocaleString()} ${count === 1 ? "event" : "events"}`;
}
