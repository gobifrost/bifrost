import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { makeAttempt, makeService } from "@/components/services/serviceTestUtils";
import { ServiceDetail } from "./ServiceDetail";

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: true }),
}));

const liveAttempt = makeAttempt({
	id: "11111111-1111-4111-8111-111111111111",
	service_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
	worker_id: "worker-1",
	state: "running",
	ready_at: "2026-09-19T09:31:00Z",
	started_at: "2026-09-19T09:30:12Z",
	heartbeat_at: "2026-09-20T08:05:00Z",
	restart_number: 1,
	created_at: "2026-09-19T09:30:12Z",
});

const liveService = makeService({
	id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
	workflow_id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
	workflow_name: "telegram_bridge",
	workflow_path: "workflows/integrations/telegram_bridge.py",
	observed_state: "running",
	active_attempt_id: liveAttempt.id,
	active_attempt: liveAttempt,
	memory_mb: 128.4,
	last_exit_reason: "requested",
	restart_count: 2,
	current_revision: "abc1234",
});

const liveAttempts = [
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
		restart_number: 0,
		created_at: "2026-09-18T12:00:05Z",
	}),
];

const mocks = vi.hoisted(() => ({
	getService: vi.fn(),
	listServiceAttempts: vi.fn(),
	listServiceLogs: vi.fn(),
}));

vi.mock("@/services/services", async (importOriginal) => ({
	...((await importOriginal()) as object),
	getService: (...args: unknown[]) => mocks.getService(...args),
	listServiceAttempts: (...args: unknown[]) =>
		mocks.listServiceAttempts(...args),
	listServiceLogs: (...args: unknown[]) =>
		mocks.listServiceLogs(...args),
	startService: vi.fn().mockResolvedValue({}),
	stopService: vi.fn().mockResolvedValue({}),
	restartService: vi.fn().mockResolvedValue({}),
	enableService: vi.fn().mockResolvedValue({}),
	disableService: vi.fn().mockResolvedValue({}),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/components/services/useServiceStream", () => ({
	useServiceStream: () => ({
		streamingLogs: [
			{
				level: "INFO",
				message: "live tail line",
				timestamp: "2026-09-21T12:00:01+00:00",
			},
		],
		isConnected: true,
	}),
}));

const mockReadFile = vi.fn();
vi.mock("@/services/fileService", () => ({
	fileService: {
		readFile: (...args: unknown[]) => mockReadFile(...args),
	},
}));

beforeEach(() => {
	vi.clearAllMocks();
	mocks.getService.mockResolvedValue(liveService);
	mocks.listServiceAttempts.mockResolvedValue({
		items: liveAttempts,
		total: liveAttempts.length,
	});
	mocks.listServiceLogs.mockResolvedValue({
		items: [
			{
				id: 1,
				service_id: liveService.id,
				attempt_id: liveAttempt.id,
				level: "INFO",
				message: "bridged 3 messages",
				timestamp: "2026-09-20T07:12:44Z",
			},
		],
		total: 1,
	});
});

function renderAt(path: string) {
	return renderWithProviders(
		<>
			<Routes>
				<Route
					path="/services/:serviceId"
					element={<ServiceDetail />}
				/>
			</Routes>
			<LocationProbe />
		</>,
		{ initialEntries: [path] },
	);
}

function LocationProbe() {
	const location = useLocation();
	return (
		<output aria-label="location">
			{location.pathname + location.search}
		</output>
	);
}

describe("ServiceDetail route", () => {
	it("renders the live detail with the attempt timeline", async () => {
		renderAt("/services/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
		expect(
			await screen.findByText("telegram_bridge"),
		).toBeInTheDocument();
		expect(mocks.getService).toHaveBeenCalledWith(
			"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		);
		expect(screen.getByText("Desired Running")).toBeInTheDocument();
		expect(
			screen.getByText(/Attempt #2 started on worker-1/),
		).toBeInTheDocument();
		expect(
			screen.getByText(/Attempt exited \(requested\)/),
		).toBeInTheDocument();
		// Persisted log output renders in the same timeline.
		expect(
			screen.getByText("bridged 3 messages"),
		).toBeInTheDocument();
		// The live tail merges below the persisted rows.
		expect(screen.getByText("live tail line")).toBeInTheDocument();
		expect(mocks.listServiceLogs).toHaveBeenCalledWith(
			"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
			{
				attemptId: undefined,
				levels: undefined,
				startDate: undefined,
				endDate: undefined,
				limit: 200,
				continuationToken: undefined,
				order: "newest_first",
			},
		);
	});

	it("pages older logs with limit/offset newest-first", async () => {
		const page = (
			startId: number,
			count: number,
			total: number,
			continuation_token: string | null,
		) => ({
			items: Array.from({ length: count }, (_, i) => ({
				id: startId + i,
				service_id: liveService.id,
				attempt_id: liveAttempt.id,
				level: "INFO",
				message: `persisted line ${startId + i}`,
				timestamp: "2026-09-20T07:12:44Z",
			})),
			total,
			continuation_token,
		});
		mocks.listServiceLogs.mockImplementation(
			(
				_serviceId: unknown,
				filters: { continuationToken?: string },
			) =>
				Promise.resolve(
					filters.continuationToken === "tok-1"
						? page(3, 1, 3, null)
						: page(1, 2, 3, "tok-1"),
				),
		);
		const { user } = renderAt(
			"/services/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		);
		expect(
			await screen.findByText("Showing 2 of 3 lines"),
		).toBeInTheDocument();
		expect(
			mocks.listServiceLogs,
		).toHaveBeenCalledWith(
			"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
			expect.objectContaining({
				limit: 200,
				continuationToken: undefined,
				order: "newest_first",
			}),
		);
		await user.click(
			screen.getByRole("button", { name: "Load older logs" }),
		);
		expect(
			await screen.findByText("Showing 3 of 3 lines"),
		).toBeInTheDocument();
		expect(
			mocks.listServiceLogs,
		).toHaveBeenCalledWith(
			"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
			expect.objectContaining({ continuationToken: "tok-1" }),
		);
		expect(
			screen.queryByRole("button", { name: "Load older logs" }),
		).not.toBeInTheDocument();
	});

	it("refreshes all live queries from the actions row", async () => {
		const { user } = renderAt(
			"/services/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		);
		await screen.findByText("telegram_bridge");
		const calls = mocks.getService.mock.calls.length;
		await user.click(screen.getByRole("button", { name: "Refresh service" }));
		await waitFor(() =>
			expect(mocks.getService.mock.calls.length).toBeGreaterThan(
				calls,
			),
		);
	});

	it("shows a not-found card that navigates back", async () => {
		mocks.getService.mockRejectedValueOnce(new Error("missing"));
		const { user } = renderAt("/services/does-not-exist");
		expect(
			await screen.findByText("Service not found"),
		).toBeInTheDocument();
		expect(
			screen.getByText(/may have been removed/),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Back to Services" }),
		);
		expect(screen.getByLabelText("location")).toHaveTextContent(
			"/services",
		);
	});

	it("retries the detail query from the not-found card", async () => {
		mocks.getService.mockRejectedValueOnce(new Error("missing"));
		const { user } = renderAt("/services/does-not-exist");
		await screen.findByText("Service not found");
		const calls = mocks.getService.mock.calls.length;
		await user.click(
			screen.getByRole("button", { name: "Try again" }),
		);
		await waitFor(() =>
			expect(mocks.getService.mock.calls.length).toBeGreaterThan(
				calls,
			),
		);
	});

	it("opens the service file in the editor", async () => {
		mockReadFile.mockResolvedValue({
			content: "x = 1",
			encoding: "utf-8",
			etag: "etag-1",
		});
		const { user } = renderAt(
			"/services/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		);
		await user.click(
			await screen.findByRole("button", { name: "Open in editor" }),
		);
		expect(mockReadFile).toHaveBeenCalledWith(
			"workflows/integrations/telegram_bridge.py",
		);
	});
});
