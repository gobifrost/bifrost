import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import {
	formatServiceMemory,
	formatServiceUptime,
	ServiceListSurface,
} from "./ServiceListSurface";
import { makeAttempt, makeService } from "./serviceTestUtils";

const telegramAttempt = makeAttempt({
	id: "11111111-1111-4111-8111-111111111111",
	service_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
	worker_id: "worker-1",
	state: "running",
	ready_at: "2026-09-19T09:31:00Z",
	started_at: "2026-09-19T09:30:12Z",
	heartbeat_at: "2026-09-20T08:05:00Z",
	restart_number: 3,
	created_at: "2026-09-19T09:30:12Z",
});

const discordAttempt = makeAttempt({
	id: "22222222-2222-4222-8222-222222222222",
	service_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
	revision: "def5678",
	worker_id: "worker-2",
	state: "starting",
	ready_at: null,
	started_at: "2026-09-20T08:04:41Z",
	heartbeat_at: "2026-09-20T08:05:00Z",
	restart_number: 0,
	created_at: "2026-09-20T08:04:41Z",
});

const services = [
	makeService({
		id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		workflow_id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
		workflow_name: "telegram_bridge",
		workflow_path: "workflows/integrations/telegram_bridge.py",
		observed_state: "running",
		active_attempt_id: telegramAttempt.id,
		active_attempt: telegramAttempt,
		memory_mb: 128.4,
		last_exit_reason: "clean_return",
		restart_count: 3,
		current_revision: "abc1234",
	}),
	makeService({
		id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
		workflow_id: "b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2",
		workflow_name: "discord_gateway",
		workflow_path: "workflows/integrations/discord_gateway.py",
		observed_state: "starting",
		active_attempt_id: discordAttempt.id,
		active_attempt: discordAttempt,
		memory_mb: 86.2,
		last_exit_reason: null,
		restart_count: 0,
		current_revision: "def5678",
	}),
	makeService({
		id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
		workflow_id: "c3c3c3c3-c3c3-4c3c-8c3c-c3c3c3c3c3c3",
		workflow_name: "mqtt_ingest",
		workflow_path: "workflows/ingest/mqtt_ingest.py",
		observed_state: "crash_loop",
		blocked_reason: "crash_loop",
		memory_mb: null,
		active_attempt_id: null,
		active_attempt: null,
		last_exit_reason: "crashed: broker connection refused",
		restart_count: 12,
		current_revision: "911abf0",
	}),
	makeService({
		id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
		workflow_id: "d4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d4",
		workflow_name: "nightly_digest",
		workflow_path: "workflows/reports/nightly_digest.py",
		startup_policy: "manual",
		restart_policy: "on_failure",
		desired_state: "stopped",
		observed_state: "stopped",
		blocked_reason: "policy",
		active_attempt_id: null,
		active_attempt: null,
		last_exit_reason: "clean_return",
		restart_count: 7,
		current_revision: "77c0ffee",
	}),
];

const surfaceProps = {
	viewMode: "table" as const,
	isPlatformAdmin: true,
	getOrgName: () => "Acme",
};

describe("formatServiceUptime", () => {
	const now = Date.parse("2026-09-20T08:05:00Z");
	it("formats minutes, hours, and days", () => {
		expect(formatServiceUptime("2026-09-20T08:04:30Z", now)).toBe("<1m");
		expect(formatServiceUptime("2026-09-20T07:00:00Z", now)).toBe(
			"1h 5m",
		);
		expect(formatServiceUptime("2026-09-17T08:05:00Z", now)).toBe(
			"3d 0h",
		);
	});
	it("returns an em dash for missing or invalid input", () => {
		expect(formatServiceUptime(null, now)).toBe("—");
		expect(formatServiceUptime("not-a-date", now)).toBe("—");
	});
});

describe("formatServiceMemory", () => {
	it("formats megabytes and gigabytes with an em dash fallback", () => {
		expect(formatServiceMemory(128.4)).toBe("128 MB");
		expect(formatServiceMemory(1536)).toBe("1.5 GB");
		expect(formatServiceMemory(null)).toBe("—");
		expect(formatServiceMemory(undefined)).toBe("—");
		expect(formatServiceMemory(Number.NaN)).toBe("—");
	});
});

describe("ServiceListSurface table", () => {
	it("renders the culled columns with a single State cell", () => {
		renderWithProviders(
			<ServiceListSurface services={services} {...surfaceProps} />,
		);
		for (const heading of [
			"Organization",
			"Service",
			"State",
			"Uptime",
			"Memory",
		]) {
			expect(
				screen.getByRole("columnheader", { name: heading }),
			).toBeInTheDocument();
		}
		for (const dropped of [
			"Desired",
			"Observed",
			"Readiness",
			"Revision",
			"Worker",
			"Last exit",
			"Restarts",
		]) {
			expect(
				screen.queryByRole("columnheader", { name: dropped }),
			).not.toBeInTheDocument();
		}
		for (const service of services) {
			expect(
				screen.getByText(service.workflow_name),
			).toBeInTheDocument();
		}
		// Source paths stay out of the table (detail view only).
		expect(
			screen.queryByText(
				"workflows/integrations/telegram_bridge.py",
			),
		).not.toBeInTheDocument();
		// Single-state badges, no sub-lines.
		expect(screen.getByText("Running")).toBeInTheDocument();
		expect(screen.getByText("Starting")).toBeInTheDocument();
		expect(screen.getByText("Crash loop")).toBeInTheDocument();
		expect(screen.queryByText("Not Ready")).not.toBeInTheDocument();
		expect(screen.queryByText("Desired Stopped")).not.toBeInTheDocument();
		// Memory from the worker heartbeat.
		expect(screen.getByText("128 MB")).toBeInTheDocument();
		expect(screen.getByText("86 MB")).toBeInTheDocument();
		// Exit reasons live on the detail header, not the table.
		expect(
			screen.queryByText("crashed: broker connection refused"),
		).not.toBeInTheDocument();
	});

	it("links names to the detail route when provided", async () => {
		const { user } = renderWithProviders(
			<ServiceListSurface
				services={services}
				{...surfaceProps}
				getDetailHref={(service) => `/services/${service.id}`}
			/>,
		);
		const link = screen.getByRole("link", { name: "mqtt_ingest" });
		expect(link).toHaveAttribute(
			"href",
			"/services/cccccccc-cccc-4ccc-8ccc-cccccccccccc",
		);
		await user.click(link);
	});

	it("fires the stop action from the row menu", async () => {
		const onAction = vi.fn();
		const { user } = renderWithProviders(
			<ServiceListSurface
				services={services}
				{...surfaceProps}
				onAction={onAction}
			/>,
		);
		const menu = screen.getByRole("button", {
			name: "telegram_bridge actions",
		});
		menu.focus();
		await user.keyboard("{Enter}");
		screen.getByRole("menuitem", { name: "Stop" }).focus();
		await user.keyboard("{Enter}");
		expect(onAction).toHaveBeenCalledExactlyOnceWith(
			services[0],
			"stop",
		);
	});

	it("disables Start for a running service", async () => {
		const { user } = renderWithProviders(
			<ServiceListSurface
				services={services}
				{...surfaceProps}
				onAction={vi.fn()}
			/>,
		);
		await user.click(
			screen.getByRole("button", { name: "telegram_bridge actions" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Start" }),
		).toHaveAttribute("aria-disabled", "true");
	});

	it("shows the empty and loading states", () => {
		const { rerender } = renderWithProviders(
			<ServiceListSurface services={[]} {...surfaceProps} />,
		);
		expect(
			screen.getByText("No services registered"),
		).toBeInTheDocument();
		rerender(
			<ServiceListSurface services={[]} {...surfaceProps} isLoading />,
		);
		expect(screen.queryByText("No services registered")).not.toBeInTheDocument();
	});
});

describe("ServiceListSurface cards", () => {
	it("renders one card per service with state and footer facts", async () => {
		const onSelect = vi.fn();
		const { user } = renderWithProviders(
			<ServiceListSurface
				services={services}
				{...surfaceProps}
				viewMode="grid"
				onSelect={onSelect}
			/>,
		);
		expect(screen.getByText("mqtt_ingest")).toBeInTheDocument();
		expect(
			screen.getAllByText("Always Restart · Automatic Startup").length,
		).toBeGreaterThan(0);
		await user.click(screen.getByText("mqtt_ingest"));
		expect(onSelect).toHaveBeenCalledExactlyOnceWith(services[2]);
	});
});
