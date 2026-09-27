import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import {
	attemptsToTimelineRows,
	ExitBadge,
	filterServiceLogs,
	mergeStreamingRows,
	ServiceDetailView,
	serviceLogsToTimelineRows,
	type ServiceTimelineRow,
} from "./ServiceDetailView";
import type { ServiceLog } from "@/services/services";
import type { StreamingLog } from "@/stores/executionStreamStore";
import { makeAttempt, makeService } from "./serviceTestUtils";

const liveAttempt = makeAttempt({
	id: "11111111-1111-4111-8111-111111111111",
	service_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
	worker_id: "worker-1",
	state: "running",
	ready_at: "2026-09-19T09:31:00Z",
	started_at: "2026-09-19T09:30:12Z",
	heartbeat_at: "2026-09-20T08:05:00Z",
	restart_number: 2,
	created_at: "2026-09-19T09:30:12Z",
});

const telegram = makeService({
	id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
	workflow_id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
	workflow_name: "telegram_bridge",
	workflow_path: "workflows/integrations/telegram_bridge.py",
	observed_state: "running",
	active_attempt_id: liveAttempt.id,
	active_attempt: liveAttempt,
	memory_mb: 128.4,
	last_exit_reason: "requested",
	restart_count: 3,
	current_revision: "abc1234",
});

// Newest-first, as GET .../attempts returns.
const telegramAttempts = [
	liveAttempt,
	makeAttempt({
		id: "11111111-1111-4111-8111-111111111110",
		service_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		worker_id: "worker-1",
		state: "stopped",
		ready_at: "2026-09-18T12:01:00Z",
		started_at: "2026-09-18T12:00:05Z",
		heartbeat_at: "2026-09-19T09:29:00Z",
		stopped_at: "2026-09-19T09:29:30Z",
		exit_code: 0,
		exit_reason: "requested",
		restart_number: 1,
		created_at: "2026-09-18T12:00:05Z",
	}),
	makeAttempt({
		id: "11111111-1111-4111-8111-111111111101",
		service_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		worker_id: "worker-1",
		state: "failed",
		ready_at: "2026-09-18T11:01:00Z",
		started_at: "2026-09-18T11:00:05Z",
		heartbeat_at: "2026-09-18T11:02:00Z",
		stopped_at: "2026-09-18T11:02:10Z",
		exit_code: 1,
		exit_reason: "crashed: broker refused",
		error: "ConnectionRefusedError: [Errno 111] broker:1883",
		restart_number: 0,
		created_at: "2026-09-18T11:00:05Z",
	}),
];

describe("attemptsToTimelineRows", () => {
	it("emits start/ready/exit system rows oldest-first", () => {
		const rows = attemptsToTimelineRows(telegramAttempts);
		expect(rows.map((r) => r.sequence)).toEqual([
			"11111111-1111-4111-8111-111111111101:started",
			"11111111-1111-4111-8111-111111111101:ready",
			"11111111-1111-4111-8111-111111111101:exited",
			"11111111-1111-4111-8111-111111111110:started",
			"11111111-1111-4111-8111-111111111110:ready",
			"11111111-1111-4111-8111-111111111110:exited",
			"11111111-1111-4111-8111-111111111111:started",
			"11111111-1111-4111-8111-111111111111:ready",
		]);
		const failed = rows.find((r) =>
			r.sequence.endsWith("111101:exited"),
		)!;
		expect(failed.level).toBe("error");
		expect(failed.message).toContain("crashed: broker refused");
		expect(failed.message).toContain("ConnectionRefusedError");
		const live = rows.filter((r) =>
			r.sequence.startsWith(liveAttempt.id),
		);
		expect(live).toHaveLength(2);
		expect(
			live[0].message,
		).toBe("Attempt #3 started on worker-1 (revision abc1234)");
	});
});

describe("serviceLogsToTimelineRows", () => {
	it("maps persisted lines to non-system rows", () => {
		const entries: ServiceLog[] = [
			{
				id: 7,
				service_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
				attempt_id: "11111111-1111-4111-8111-111111111111",
				level: "INFO",
				message: "bridged 3 messages",
				timestamp: "2026-09-20T07:12:44Z",
			},
		];
		expect(serviceLogsToTimelineRows(entries)).toEqual([
			{
				sequence: "log:7",
				attempt_id: "11111111-1111-4111-8111-111111111111",
				level: "INFO",
				message: "bridged 3 messages",
				timestamp: "2026-09-20T07:12:44Z",
				system: false,
			},
		]);
	});
});

describe("mergeStreamingRows", () => {
	const persisted: ServiceTimelineRow[] = [
		{
			sequence: "log:7",
			attempt_id: "att-1",
			level: "INFO",
			message: "already flushed",
			timestamp: "2026-09-21T12:00:00+00:00",
			system: false,
		},
	];
	const streaming: StreamingLog[] = [
		{
			level: "INFO",
			message: "already flushed",
			timestamp: "2026-09-21T12:00:00+00:00",
		},
		{
			level: "INFO",
			message: "fresh tail",
			timestamp: "2026-09-21T12:00:01+00:00",
		},
	];
	it("drops streamed lines that already persisted", () => {
		const merged = mergeStreamingRows(persisted, streaming, "att-1");
		expect(merged.map((r) => r.message)).toEqual([
			"already flushed",
			"fresh tail",
		]);
		const fresh = merged[1];
		expect(fresh.system).toBe(false);
		expect(fresh.attempt_id).toBe("att-1");
		expect(fresh.sequence).toMatch(/^stream:/);
	});

	it("keeps identical triples from different attempts", () => {
		const merged = mergeStreamingRows(persisted, streaming, "att-2");
		expect(merged.map((r) => r.message)).toEqual([
			"already flushed",
			"already flushed",
			"fresh tail",
		]);
	});
});

describe("filterServiceLogs", () => {
	const lines: ServiceTimelineRow[] = [
		{
			sequence: "att-1:started",
			timestamp: "2026-09-19T09:30:12Z",
			level: "info",
			message: "Attempt #1 started",
			attempt_id: "att-1",
			system: true,
		},
		{
			sequence: "att-2:exited",
			timestamp: "2026-09-20T07:12:44Z",
			level: "error",
			message: "boom happened",
			attempt_id: "att-2",
			system: true,
		},
	];
	const base = {
		attemptId: "all",
		level: "all",
		search: "",
		dateRange: undefined,
	};
	it("passes everything unfiltered", () => {
		expect(filterServiceLogs(lines, base)).toHaveLength(2);
	});
	it("filters by attempt, level, and search text", () => {
		expect(
			filterServiceLogs(lines, { ...base, attemptId: "att-2" }),
		).toHaveLength(1);
		expect(
			filterServiceLogs(lines, { ...base, level: "error" }),
		).toHaveLength(1);
		expect(
			filterServiceLogs(lines, { ...base, search: "STARTED" }),
		).toHaveLength(1);
	});
	it("filters by date range at day granularity", () => {
		// Day bounds are local-calendar days; keep the lines several days
		// apart so the expectation holds in every timezone.
		const wide: ServiceTimelineRow[] = [
			{
				sequence: "att-1:started",
				timestamp: "2026-09-14T09:30:12Z",
				level: "info",
				message: "old line",
				attempt_id: "att-1",
				system: true,
			},
			{
				sequence: "att-2:exited",
				timestamp: "2026-09-20T07:12:44Z",
				level: "error",
				message: "boom happened",
				attempt_id: "att-2",
				system: true,
			},
		];
		const day = new Date("2026-09-20T07:12:44Z");
		expect(
			filterServiceLogs(wide, {
				...base,
				dateRange: { from: day, to: day },
			}).map((l) => l.sequence),
		).toEqual(["att-2:exited"]);
		expect(
			filterServiceLogs(wide, {
				...base,
				dateRange: {
					from: new Date(day.getTime() + 5 * 86_400_000),
					to: undefined,
				},
			}),
		).toHaveLength(0);
	});
});

describe("ExitBadge", () => {
	it("grades clean, requested, and failure exits", () => {
		const { rerender } = renderWithProviders(
			<ExitBadge reason="clean_return" />,
		);
		expect(screen.getByText("clean_return")).toBeInTheDocument();
		rerender(<ExitBadge reason="crashed: broker refused" />);
		expect(
			screen.getByText("crashed: broker refused"),
		).toBeInTheDocument();
	});
});

describe("ServiceDetailView", () => {
	const viewProps = {
		logEntries: [
			{
				id: 7,
				service_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
				attempt_id: "11111111-1111-4111-8111-111111111111",
				level: "INFO",
				message: "bridged 3 messages",
				timestamp: "2026-09-20T07:12:44Z",
			},
		] as ServiceLog[],
		logsTotal: 1,
		hasOlderLogs: false,
		isLoadingOlderLogs: false,
		onLoadOlderLogs: () => {},
		dateRange: undefined,
		onDateRangeChange: () => {},
		streamingLogs: [
			{
				level: "INFO",
				message: "fresh tail",
				timestamp: "2026-09-21T12:00:01+00:00",
			},
		],
		isStreamConnected: true,
		onRefresh: () => {},
		isRefreshing: false,
	};

	it("renders state, icon actions, and the unified timeline", () => {
		renderWithProviders(
			<ServiceDetailView
				service={telegram}
				attempts={telegramAttempts}
				onOpenInEditor={vi.fn()}
				{...viewProps}
			/>,
		);
		expect(screen.getByText("telegram_bridge")).toBeInTheDocument();
		// Header carries state as words + badges (no state card).
		expect(screen.queryByText("State")).not.toBeInTheDocument();
		expect(screen.getByText("Desired Running")).toBeInTheDocument();
		expect(screen.getByText("worker-1")).toBeInTheDocument();
		expect(screen.getByText(/3 restarts/)).toBeInTheDocument();
		expect(screen.getByText("128 MB")).toBeInTheDocument();
		expect(screen.getByText(/Always Restart/)).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Open in editor" }),
		).toBeInTheDocument();
		for (const name of ["Start", "Stop", "Restart", "Enable", "Disable"]) {
			expect(
				screen.getByRole("button", { name }),
			).toBeInTheDocument();
		}
		expect(
			screen.getByRole("button", { name: "Pick a date range" }),
		).toBeInTheDocument();
		// Attempts live in the timeline: no separate tab, no list, no picker.
		expect(screen.queryByRole("tab")).not.toBeInTheDocument();
		expect(screen.queryByRole("tabpanel")).not.toBeInTheDocument();
		expect(
			screen.queryByRole("combobox", {
				name: "Filter logs by attempt",
			}),
		).not.toBeInTheDocument();
		// No variables or result panels on the service detail view.
		expect(screen.queryByText(/variable/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/result/i)).not.toBeInTheDocument();
		// Attempt lifecycle renders as system rows, oldest first.
		expect(
			screen.getByText(/Attempt #1 started on worker-1/),
		).toBeInTheDocument();
		expect(
			screen.getByText(/Attempt exited \(crashed: broker refused\)/),
		).toBeInTheDocument();
		expect(
			screen.getByText(/Attempt exited \(requested\)/),
		).toBeInTheDocument();
		// Persisted log output merges into the same timeline (non-system).
		expect(
			screen.getByText("bridged 3 messages"),
		).toBeInTheDocument();
		// The live tail appends below the persisted rows.
		expect(screen.getByText("fresh tail")).toBeInTheDocument();
		// Newest-first display: the live tail renders above older rows.
		const fresh = screen.getByText("fresh tail");
		const older = screen.getByText("bridged 3 messages");
		expect(
			fresh.compareDocumentPosition(older) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		// Pagination label reflects persisted lines, not system rows.
		expect(screen.getByText("Showing 1 of 1 lines")).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Load older logs" }),
		).not.toBeInTheDocument();
	});

	it("offers load-older pagination when the total exceeds the page", async () => {
		const onLoadOlderLogs = vi.fn();
		const { user } = renderWithProviders(
			<ServiceDetailView
				service={telegram}
				attempts={telegramAttempts}
				{...viewProps}
				logsTotal={5}
				hasOlderLogs
				onLoadOlderLogs={onLoadOlderLogs}
			/>,
		);
		expect(screen.getByText("Showing 1 of 5 lines")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Load older logs" }),
		);
		expect(onLoadOlderLogs).toHaveBeenCalledTimes(1);
	});

	it("disables Start and fires Stop with an icon button", async () => {
		const onAction = vi.fn();
		const { user } = renderWithProviders(
			<ServiceDetailView
				service={telegram}
				attempts={telegramAttempts}
				onAction={onAction}
				{...viewProps}
				logEntries={[]}
				logsTotal={0}
				hasOlderLogs={false}
				isLoadingOlderLogs={false}
				onLoadOlderLogs={() => {}}
				streamingLogs={[]}
			/>,
		);
		expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
		await user.click(screen.getByRole("button", { name: "Stop" }));
		expect(onAction).toHaveBeenCalledExactlyOnceWith(telegram, "stop");
	});

	it("filters by level and search text through the shared logs panel", async () => {
		const { user } = renderWithProviders(
			<ServiceDetailView
				service={telegram}
				attempts={telegramAttempts}
				{...viewProps}
				logEntries={[]}
				logsTotal={0}
				hasOlderLogs={false}
				isLoadingOlderLogs={false}
				onLoadOlderLogs={() => {}}
				streamingLogs={[]}
			/>,
		);
		await user.click(
			screen.getByRole("combobox", { name: "Log level" }),
		);
		await user.click(screen.getByRole("option", { name: "Error" }));
		await user.type(
			screen.getByRole("textbox", { name: "Search logs" }),
			"broker refused",
		);
		expect(
			screen.getByText(/Attempt exited \(crashed: broker refused\)/),
		).toBeInTheDocument();
		expect(
			screen.queryByText(/Service reported ready/),
		).not.toBeInTheDocument();
	});

	it("refreshes from the actions row", async () => {
		const onRefresh = vi.fn();
		const { user } = renderWithProviders(
			<ServiceDetailView
				service={telegram}
				attempts={telegramAttempts}
				{...viewProps}
				logEntries={[]}
				streamingLogs={[]}
				onRefresh={onRefresh}
				isRefreshing={false}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Refresh service" }));
		expect(onRefresh).toHaveBeenCalledTimes(1);
	});

	it("navigates back to the list", async () => {
		const onBack = vi.fn();
		const { user } = renderWithProviders(
			<ServiceDetailView
				service={telegram}
				attempts={telegramAttempts}
				onBack={onBack}
				{...viewProps}
				logEntries={[]}
				logsTotal={0}
				hasOlderLogs={false}
				isLoadingOlderLogs={false}
				onLoadOlderLogs={() => {}}
				streamingLogs={[]}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Back to services" }));
		expect(onBack).toHaveBeenCalledTimes(1);
	});
});
