/**
 * Shared formatting utilities for usage report components.
 */

import { format } from "date-fns";

/**
 * Format the primitive date labels supplied by chart data.
 */
export function formatChartDateLabel(label: unknown): string {
	if (typeof label !== "string" && typeof label !== "number") {
		throw new TypeError("Chart date label must be a string or number");
	}
	return format(new Date(label), "PPP");
}

/**
 * Format a number as currency (USD)
 */
export function formatCurrency(value: string | number | undefined): string {
	if (value === undefined || value === null) return "$0.00";
	const numValue = typeof value === "string" ? parseFloat(value) : value;
	if (isNaN(numValue)) return "$0.00";
	return numValue.toLocaleString("en-US", {
		style: "currency",
		currency: "USD",
	});
}

/**
 * Format a number with thousand separators
 */
export function formatNumber(value: number | undefined): string {
	if (value === undefined || value === null) return "0";
	return value.toLocaleString();
}

/**
 * Format bytes to human-readable size (KB, MB, GB)
 */
export function formatBytes(bytes: number | undefined): string {
	if (bytes === undefined || bytes === null || bytes === 0) return "0 B";
	const units = ["B", "KB", "MB", "GB", "TB"];
	const k = 1024;
	const i = Math.floor(Math.log(bytes) / Math.log(k));
	const size = bytes / Math.pow(k, i);
	return `${size.toFixed(i > 0 ? 2 : 0)} ${units[i]}`;
}

/**
 * Format seconds to human-readable time
 */
export function formatCpuSeconds(seconds: number | undefined): string {
	if (seconds === undefined || seconds === null) return "0s";
	if (seconds < 60) return `${seconds.toFixed(1)}s`;
	if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
	return `${(seconds / 3600).toFixed(2)}h`;
}

/**
 * Sort configuration used across usage tables.
 */
export type SortConfig = { by: string; dir: "asc" | "desc" };
