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

export function formatCount(value: number): string {
	return value.toLocaleString();
}
