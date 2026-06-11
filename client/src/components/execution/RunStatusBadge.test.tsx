/**
 * Component tests for RunStatusBadge — the History feed's quiet-success /
 * loud-failure status badge. The visual hierarchy IS the contract here:
 * success must render as a quiet outline (no loud green fill), failures
 * must use the destructive variant, and Scheduled exposes its fire time
 * via a title tooltip.
 */

import { describe, it, expect } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { RunStatusBadge } from "./RunStatusBadge";

describe("RunStatusBadge — labels", () => {
	it.each([
		["Success", /^completed$/i],
		["Failed", /^failed$/i],
		["Timeout", /timed out/i],
		["CompletedWithErrors", /completed with errors/i],
		["Running", /running/i],
		["Pending", /pending/i],
		["Scheduled", /scheduled/i],
		["Cancelling", /cancelling/i],
		["Cancelled", /cancelled/i],
	])("renders %s with a readable label", (status, expectedLabel) => {
		renderWithProviders(<RunStatusBadge status={status} />);
		expect(screen.getByText(expectedLabel)).toBeInTheDocument();
	});

	it("falls back to the raw status string for unknown statuses", () => {
		renderWithProviders(<RunStatusBadge status="SomethingNew" />);
		expect(screen.getByText("SomethingNew")).toBeInTheDocument();
	});
});

describe("RunStatusBadge — visual hierarchy", () => {
	it("renders Success quietly: no solid green fill, muted text", () => {
		renderWithProviders(<RunStatusBadge status="Success" />);
		const badge = screen.getByText(/^completed$/i);
		expect(badge.className).not.toMatch(/bg-green/);
		expect(badge.className).toMatch(/text-muted-foreground/);
	});

	it("renders Failed loudly with the destructive variant", () => {
		renderWithProviders(<RunStatusBadge status="Failed" />);
		const badge = screen.getByText(/^failed$/i);
		expect(badge.className).toMatch(/destructive/);
	});

	it("renders Timeout with the destructive variant", () => {
		renderWithProviders(<RunStatusBadge status="Timeout" />);
		const badge = screen.getByText(/timed out/i);
		expect(badge.className).toMatch(/destructive/);
	});
});

describe("RunStatusBadge — scheduled tooltip", () => {
	it("exposes the scheduled fire time via a title attribute", () => {
		renderWithProviders(
			<RunStatusBadge
				status="Scheduled"
				scheduledAt="2030-01-01T09:00:00Z"
			/>,
		);
		const badge = screen.getByText(/scheduled/i);
		expect(badge).toHaveAttribute(
			"title",
			expect.stringContaining("Scheduled for"),
		);
	});

	it("omits the title when no scheduledAt is provided", () => {
		renderWithProviders(<RunStatusBadge status="Scheduled" />);
		const badge = screen.getByText(/scheduled/i);
		expect(badge).not.toHaveAttribute("title");
	});
});

describe("RunStatusBadge — pending wait variants", () => {
	it("shows queue position when waiting in queue", () => {
		renderWithProviders(
			<RunStatusBadge
				status="Pending"
				waitReason="queued"
				queuePosition={3}
			/>,
		);
		expect(screen.getByText(/queued — position 3/i)).toBeInTheDocument();
	});

	it("shows memory figures under memory pressure", () => {
		renderWithProviders(
			<RunStatusBadge
				status="Pending"
				waitReason="memory_pressure"
				availableMemoryMb={512}
				requiredMemoryMb={1024}
			/>,
		);
		expect(
			screen.getByText(/heavy load \(512MB \/ 1024MB\)/i),
		).toBeInTheDocument();
	});

	it("falls back to plain Pending without wait metadata", () => {
		renderWithProviders(<RunStatusBadge status="Pending" />);
		expect(screen.getByText(/^pending$/i)).toBeInTheDocument();
	});
});
