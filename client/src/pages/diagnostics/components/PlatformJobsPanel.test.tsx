import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	getPlatformJobs: vi.fn(),
	cancelPlatformJob: vi.fn(),
	onAnyPlatformJobUpdate: vi.fn(() => vi.fn()),
}));

vi.mock("@/services/platformJobs", () => ({
	getPlatformJobs: (...args: unknown[]) => mocks.getPlatformJobs(...args),
	cancelPlatformJob: (...args: unknown[]) => mocks.cancelPlatformJob(...args),
}));

vi.mock("@/services/websocket", () => ({
	webSocketService: { onAnyPlatformJobUpdate: mocks.onAnyPlatformJobUpdate },
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { PlatformJobsPanel } from "./PlatformJobsPanel";

const queuedJob = {
	id: "10000000-0000-0000-0000-000000000001",
	job_type: "solution.deploy",
	payload_version: 1,
	organization_id: null,
	resource_type: "solution_deploy",
	resource_id: "deploy-1",
	resource_lock_key: "solution:solution-1",
	priority: 500,
	title: "Solution deploy",
	action_url: "/solutions/solution-1",
	requested_by_user_id: "user-1",
	requested_by_name: "Ada Lovelace",
	status: "queued",
	progress: {
		phase: "Waiting for scheduler memory",
		current: 0,
		total: null,
		percent: 0,
	},
	revision: 2,
	attempt: 0,
	max_attempts: 2,
	can_cancel: true,
	result: null,
	error: null,
	notification_id: null,
	memory_start_bytes: null,
	memory_peak_bytes: null,
	memory_limit_bytes: null,
	memory_required_bytes: 768 * 1024 * 1024,
	started_at: null,
	completed_at: null,
	created_at: "2026-08-23T16:58:11Z",
	updated_at: "2026-08-23T16:58:13Z",
};

function renderPanel() {
	const client = new QueryClient({
		defaultOptions: {
			queries: { retry: false },
			mutations: { retry: false },
		},
	});
	return render(
		<MemoryRouter>
			<QueryClientProvider client={client}>
				<PlatformJobsPanel availableMemoryBytes={749 * 1024 * 1024} />
			</QueryClientProvider>
		</MemoryRouter>,
	);
}

describe("PlatformJobsPanel", () => {
	beforeEach(() => {
		mocks.getPlatformJobs.mockReset();
		mocks.cancelPlatformJob.mockReset();
		mocks.onAnyPlatformJobUpdate.mockClear();
		mocks.getPlatformJobs.mockResolvedValue({
			jobs: [queuedJob],
			total: 1,
			limit: 25,
			offset: 0,
		});
		mocks.cancelPlatformJob.mockResolvedValue({
			accepted: true,
			job: {
				...queuedJob,
				status: "cancelled",
				can_cancel: false,
				revision: 3,
				progress: { ...queuedJob.progress, phase: "Cancelled" },
			},
		});
	});

	it("shows on-demand jobs with an explainable memory wait and details", async () => {
		const user = userEvent.setup();
		renderPanel();

		expect(await screen.findByText("Solution deploy")).toBeInTheDocument();
		expect(screen.getByText("749 MiB available")).toBeInTheDocument();
		expect(screen.getByText("768 MiB required")).toBeInTheDocument();
		expect(
			screen.getByText("Waiting for scheduler memory"),
		).toBeInTheDocument();
		expect(
			screen
				.getAllByRole("columnheader")
				.map((header) => header.textContent),
		).toEqual(["Name", "State", "Elapsed", "Memory"]);
		expect(
			screen.queryByRole("columnheader", { name: "Attempts" }),
		).not.toBeInTheDocument();
		expect(mocks.getPlatformJobs).toHaveBeenCalledWith(
			expect.objectContaining({
				activeOnly: false,
				limit: 25,
				offset: 0,
			}),
		);

		await user.click(
			screen.getByRole("row", {
				name: "View Solution deploy platform job",
			}),
		);
		const drawer = await screen.findByRole("dialog");
		expect(within(drawer).getByText("Ada Lovelace")).toBeInTheDocument();
		expect(within(drawer).getByText("768 MiB")).toBeInTheDocument();
		expect(
			within(drawer).getByRole("link", { name: /Open resource/ }),
		).toHaveAttribute("href", "/solutions/solution-1");
	});

	it("pages and searches the full server-side job history", async () => {
		const user = userEvent.setup();
		let resolveNextPage: (value: {
			jobs: (typeof queuedJob)[];
			total: number;
			limit: number;
			offset: number;
		}) => void = () => undefined;
		const firstPage = {
			jobs: [queuedJob],
			total: 26,
			limit: 25,
			offset: 0,
		};
		const secondPage = {
			jobs: [
				{
					...queuedJob,
					id: "10000000-0000-0000-0000-000000000002",
					title: "Second page deploy",
				},
			],
			total: 26,
			limit: 25,
			offset: 25,
		};
		mocks.getPlatformJobs
			.mockResolvedValueOnce(firstPage)
			.mockImplementationOnce(
				() =>
					new Promise((resolve) => {
						resolveNextPage = resolve;
					}),
			)
			.mockResolvedValue(firstPage);
		renderPanel();

		expect(await screen.findByText("Page 1 of 2")).toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: "Page 1" }),
		).not.toBeInTheDocument();
		const scrollContainer = screen
			.getByRole("columnheader", { name: "Name" })
			.closest("table")?.parentElement;
		expect(scrollContainer).not.toBeNull();
		if (scrollContainer) scrollContainer.scrollTop = 72;

		await user.click(await screen.findByLabelText("Go to next page"));
		await waitFor(() =>
			expect(mocks.getPlatformJobs).toHaveBeenCalledWith(
				expect.objectContaining({ offset: 25 }),
			),
		);
		expect(screen.getByText("Solution deploy")).toBeInTheDocument();
		expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
		expect(
			screen.getByRole("columnheader", { name: "Name" }).closest("table")
				?.parentElement,
		).toBe(scrollContainer);
		expect(scrollContainer?.scrollTop).toBe(72);

		await act(async () => resolveNextPage(secondPage));
		expect(
			await screen.findByText("Second page deploy"),
		).toBeInTheDocument();
		expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();

		await user.type(
			screen.getByLabelText("Search Platform Jobs"),
			"deploy",
		);
		await waitFor(() =>
			expect(mocks.getPlatformJobs).toHaveBeenCalledWith(
				expect.objectContaining({ offset: 0, search: "deploy" }),
			),
		);
	});

	it("confirms cancellation through the shared job endpoint", async () => {
		const user = userEvent.setup();
		renderPanel();
		await user.click(
			await screen.findByRole("row", {
				name: "View Solution deploy platform job",
			}),
		);
		await user.click(screen.getByRole("button", { name: "Cancel job" }));

		const confirmation = await screen.findByRole("alertdialog");
		await user.click(
			within(confirmation).getByRole("button", { name: "Cancel job" }),
		);

		expect(mocks.cancelPlatformJob.mock.calls[0][0]).toBe(queuedJob.id);
		expect(
			(await screen.findAllByText("Cancelled")).length,
		).toBeGreaterThan(0);
	});
});
