import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router-dom";
import { waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { makeAttempt, makeService } from "@/components/services/serviceTestUtils";
import { Services } from "./Services";

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

/** Live-shaped list payload (embedded attempt summary, exit, memory). */
const liveServices = [
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

const mocks = vi.hoisted(() => ({
	listServices: vi.fn(),
	startService: vi.fn(),
	stopService: vi.fn(),
	restartService: vi.fn(),
	enableService: vi.fn(),
	disableService: vi.fn(),
}));

vi.mock("@/services/services", async (importOriginal) => ({
	...((await importOriginal()) as object),
	listServices: (...args: unknown[]) => mocks.listServices(...args),
	startService: (...args: unknown[]) => mocks.startService(...args),
	stopService: (...args: unknown[]) => mocks.stopService(...args),
	restartService: (...args: unknown[]) => mocks.restartService(...args),
	enableService: (...args: unknown[]) => mocks.enableService(...args),
	disableService: (...args: unknown[]) => mocks.disableService(...args),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const mockUseAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useIsDesktop: () => true,
}));

beforeEach(() => {
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	vi.clearAllMocks();
	mocks.listServices.mockResolvedValue({
		items: liveServices,
		total: liveServices.length,
	});
});

function LocationProbe() {
	const location = useLocation();
	return (
		<output aria-label="location">
			{location.pathname + location.search}
		</output>
	);
}

async function renderServicesPage() {
	return renderWithProviders(
		<>
			<LocationProbe />
			<Services />
		</>,
	);
}

describe("Services page (live)", () => {
	it("renders the live services list with search and filters", async () => {
		await renderServicesPage();
		expect(
			await screen.findByText("telegram_bridge"),
		).toBeInTheDocument();
		expect(screen.getByText("Crash loop")).toBeInTheDocument();
		expect(
			screen.getByRole("textbox", {
				name: "Search by name or source path...",
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole("combobox", {
				name: "Filter services by state",
			}),
		).toBeInTheDocument();
	});

	it("navigates to the service detail route on row click", async () => {
		const { user } = await renderServicesPage();
		await user.click(
			await screen.findByRole("link", { name: "mqtt_ingest" }),
		);
		expect(
			screen.getByLabelText("location"),
		).toHaveTextContent("/services/cccccccc-cccc-4ccc-8ccc-cccccccccccc");
	});

	it("shows a retryable error when the live list fails", async () => {
		mocks.listServices.mockRejectedValueOnce(new Error("boom"));
		const { user } = await renderServicesPage();
		expect(
			await screen.findByText("Couldn't load services."),
		).toBeInTheDocument();
		mocks.listServices.mockResolvedValue({
			items: liveServices,
			total: liveServices.length,
		});
		await user.click(
			screen.getByRole("button", { name: "Retry loading" }),
		);
		expect(
			await screen.findByText("telegram_bridge"),
		).toBeInTheDocument();
	});

	it("refreshes the live list from the Refresh button", async () => {
		const { user } = await renderServicesPage();
		await screen.findByText("telegram_bridge");
		const calls = mocks.listServices.mock.calls.length;
		await user.click(
			screen.getByRole("button", { name: "Refresh services" }),
		);
		await waitFor(() =>
			expect(mocks.listServices.mock.calls.length).toBeGreaterThan(
				calls,
			),
		);
	});

	it("filters services by name", async () => {
		const { user } = await renderServicesPage();
		await screen.findByText("telegram_bridge");
		await user.type(
			screen.getByRole("textbox", {
				name: "Search by name or source path...",
			}),
			"discord",
		);
		await vi.waitFor(() =>
			expect(
				screen.queryByText("telegram_bridge"),
			).not.toBeInTheDocument(),
		);
		expect(screen.getByText("discord_gateway")).toBeInTheDocument();
	});

	it("filters services by state", async () => {
		const { user } = await renderServicesPage();
		await screen.findByText("telegram_bridge");
		await user.click(
			screen.getByRole("combobox", {
				name: "Filter services by state",
			}),
		);
		await user.click(screen.getByRole("option", { name: "Crash loop" }));
		expect(screen.getByText("mqtt_ingest")).toBeInTheDocument();
		expect(screen.queryByText("telegram_bridge")).not.toBeInTheDocument();
	});

	it("runs Start directly against the live endpoint (no confirm)", async () => {
		mocks.startService.mockResolvedValue({
			id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
		});
		const { user } = await renderServicesPage();
		await screen.findByRole("button", {
			name: "nightly_digest actions",
		});
		await user.click(
			screen.getByRole("button", {
				name: "nightly_digest actions",
			}),
		);
		await user.click(screen.getByRole("menuitem", { name: "Start" }));
		await waitFor(() =>
			expect(mocks.startService).toHaveBeenCalledExactlyOnceWith(
				"dddddddd-dddd-4ddd-8ddd-dddddddddddd",
			),
		);
		expect(
			screen.queryByText("Start nightly_digest?"),
		).not.toBeInTheDocument();
	});

	it("confirms Stop through the dialog before calling the live endpoint", async () => {
		mocks.stopService.mockResolvedValue({
			id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		});
		const { user } = await renderServicesPage();
		await screen.findByRole("button", {
			name: "telegram_bridge actions",
		});
		await user.click(
			screen.getByRole("button", {
				name: "telegram_bridge actions",
			}),
		);
		await user.click(screen.getByRole("menuitem", { name: "Stop" }));
		expect(
			screen.getByText("Stop telegram_bridge?"),
		).toBeInTheDocument();
		expect(mocks.stopService).not.toHaveBeenCalled();
		await user.click(
			screen.getByRole("button", { name: "Stop service" }),
		);
		await waitFor(() =>
			expect(mocks.stopService).toHaveBeenCalledExactlyOnceWith(
				"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
			),
		);
		await waitFor(() =>
			expect(
				screen.queryByText("Stop telegram_bridge?"),
			).not.toBeInTheDocument(),
		);
	});
});
