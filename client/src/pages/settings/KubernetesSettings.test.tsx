import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	getKubernetesStatus: vi.fn(),
	getKubernetesExecution: vi.fn(),
	getWorkerCapacityStatus: vi.fn(),
	updateKubernetesExecution: vi.fn(),
	updateKubernetesPolicy: vi.fn(),
}));

vi.mock("@/services/kubernetes", async (importOriginal) => ({
	KubernetesRequestError: (
		await importOriginal<typeof import("@/services/kubernetes")>()
	).KubernetesRequestError,
	getKubernetesExecution: (...args: unknown[]) =>
		mocks.getKubernetesExecution(...args),
	getKubernetesStatus: (...args: unknown[]) =>
		mocks.getKubernetesStatus(...args),
	getWorkerCapacityStatus: (...args: unknown[]) =>
		mocks.getWorkerCapacityStatus(...args),
	updateKubernetesExecution: (...args: unknown[]) =>
		mocks.updateKubernetesExecution(...args),
	updateKubernetesPolicy: (...args: unknown[]) =>
		mocks.updateKubernetesPolicy(...args),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { KubernetesRequestError } from "@/services/kubernetes";
import { KubernetesSettings } from "./KubernetesSettings";

const policy = {
	warm_workers: 1,
	reserved_fast_path_workers: 0,
	auto_priority_app_workflows: false,
	max_workers: 6,
	worker_memory_mib: 2048,
	largest_worker_mib: 8192,
	idle_seconds: 300,
	isolated_jobs_enabled: false,
	max_isolated_pods: 2,
};

const settings = {
	policy,
	fast_path_ready_workers: 0,
	imported_from_env: {},
	unread_variables: [],
	job_types: [
		{
			job_type: "application.deploy",
			title: "App Deploys",
			description: "Compiles apps in an isolated pod.",
			enabled: true,
			default_enabled: true,
		},
		{
			job_type: "application.sdk_update",
			title: "App SDK Rebuilds",
			description: "Rebuilds the SDK in an isolated pod.",
			enabled: false,
			default_enabled: true,
		},
	],
};

describe("KubernetesSettings", () => {
	beforeEach(() => {
		mocks.getKubernetesStatus.mockReset();
		mocks.getKubernetesStatus.mockResolvedValue({ configured: true });
		mocks.getKubernetesExecution.mockReset();
		mocks.updateKubernetesExecution.mockReset();
		mocks.updateKubernetesPolicy.mockReset();
		mocks.getKubernetesExecution.mockResolvedValue(settings);
		mocks.getWorkerCapacityStatus.mockReset();
		mocks.getWorkerCapacityStatus.mockResolvedValue({
			configured: false,
		});
	});

	it("keeps scaling and concurrency editable without Kubernetes, while disabling pod controls", async () => {
		mocks.getKubernetesStatus.mockResolvedValue({ configured: false });
		render(<KubernetesSettings />);
		expect(await screen.findByLabelText("Max Workers")).toBeEnabled();
		expect(
			screen.getByText(/Kubernetes isolation is unavailable/),
		).toBeVisible();
		expect(screen.getByLabelText("Isolate Build Jobs")).toBeDisabled();
		expect(screen.getByLabelText("Max Isolated Pods")).toBeDisabled();
		expect(screen.getByLabelText("App Deploys")).toBeDisabled();
		for (const input of screen.getAllByLabelText("Max Parallel Runs")) {
			expect(input).toBeEnabled();
		}
	});

	it("shows the scaling and isolation policy", async () => {
		render(<KubernetesSettings />);

		expect(await screen.findByLabelText("Max Workers")).toHaveValue(6);
		expect(screen.getByLabelText("Warm Workers")).toHaveValue(1);
		expect(screen.getByLabelText("Reserved Fast Path Workers")).toHaveValue(
			0,
		);
		expect(
			screen.getByLabelText("Prioritize App Workflows"),
		).not.toBeChecked();
		expect(screen.getByLabelText("Worker Memory (MiB)")).toHaveValue(2048);
		expect(screen.getByLabelText("Largest Worker (MiB)")).toHaveValue(8192);
		expect(screen.getByLabelText("Idle Time (Seconds)")).toHaveValue(300);
		expect(screen.getByLabelText("Max Isolated Pods")).toHaveValue(2);
		expect(screen.getByLabelText("Isolate Build Jobs")).not.toBeChecked();
		expect(screen.getByText("App Deploys")).toBeInTheDocument();
		expect(screen.getByText("App SDK Rebuilds")).toBeInTheDocument();
	});

	it("saves only the changed scaling fields", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockResolvedValue({
			...settings,
			policy: { ...policy, max_workers: 10 },
		});
		render(<KubernetesSettings />);

		const input = await screen.findByLabelText("Max Workers");
		await user.clear(input);
		await user.type(input, "10");
		await user.click(screen.getByRole("button", { name: "Save Scaling" }));

		await waitFor(() =>
			expect(mocks.updateKubernetesPolicy).toHaveBeenCalledWith({
				max_workers: 10,
			}),
		);
		expect(
			screen.getByRole("button", { name: "Save Scaling" }),
		).toBeDisabled();
	});

	it("reserves Fast Path capacity without overwriting Normal scaling", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockResolvedValue({
			...settings,
			policy: { ...policy, reserved_fast_path_workers: 2 },
		});
		render(<KubernetesSettings />);
		const input = await screen.findByLabelText(
			"Reserved Fast Path Workers",
		);
		await user.clear(input);
		await user.type(input, "2");
		await user.click(screen.getByRole("button", { name: "Save Scaling" }));
		await waitFor(() =>
			expect(mocks.updateKubernetesPolicy).toHaveBeenCalledWith({
				reserved_fast_path_workers: 2,
			}),
		);
		expect(screen.getByLabelText("Warm Workers")).toHaveValue(1);
		expect(screen.getByLabelText("Max Workers")).toHaveValue(6);
	});

	it("shows a refused reservation inline and preserves the edit", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockRejectedValue(
			new KubernetesRequestError(
				"Leave capacity for Normal work.",
				422,
				"reserved_fast_path_workers",
			),
		);
		render(<KubernetesSettings />);
		const input = await screen.findByLabelText(
			"Reserved Fast Path Workers",
		);
		await user.clear(input);
		await user.type(input, "6");
		await user.click(screen.getByRole("button", { name: "Save Scaling" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Leave capacity for Normal work.",
		);
		expect(input).toHaveValue(6);
		expect(input).toHaveAttribute("aria-invalid", "true");
	});

	it("enables App priority only after the server accepts the change", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockResolvedValue({
			...settings,
			policy: { ...policy, auto_priority_app_workflows: true },
		});
		render(<KubernetesSettings />);
		await user.click(
			await screen.findByRole("switch", {
				name: "Prioritize App Workflows",
			}),
		);
		await waitFor(() =>
			expect(mocks.updateKubernetesPolicy).toHaveBeenCalledWith({
				auto_priority_app_workflows: true,
			}),
		);
		expect(
			screen.getByRole("switch", { name: "Prioritize App Workflows" }),
		).toBeChecked();
	});

	it("preserves an unsaved reservation while App priority is saved", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockResolvedValue({
			...settings,
			policy: { ...policy, auto_priority_app_workflows: true },
		});
		render(<KubernetesSettings />);
		const reservation = await screen.findByLabelText(
			"Reserved Fast Path Workers",
		);
		await user.clear(reservation);
		await user.type(reservation, "2");
		await user.click(
			screen.getByRole("switch", { name: "Prioritize App Workflows" }),
		);
		await waitFor(() =>
			expect(
				screen.getByRole("switch", {
					name: "Prioritize App Workflows",
				}),
			).toBeChecked(),
		);
		expect(reservation).toHaveValue(2);
		expect(
			screen.getByRole("button", { name: "Save Scaling" }),
		).toBeEnabled();
	});

	it("keeps App priority off when the reservation prerequisite is refused", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockRejectedValue(
			new KubernetesRequestError(
				"Configure Fast Path capacity first.",
				422,
			),
		);
		render(<KubernetesSettings />);
		const toggle = await screen.findByRole("switch", {
			name: "Prioritize App Workflows",
		});
		await user.click(toggle);
		await waitFor(() =>
			expect(mocks.updateKubernetesPolicy).toHaveBeenCalledTimes(1),
		);
		await waitFor(() => expect(toggle).toBeEnabled());
		expect(toggle).not.toBeChecked();
	});

	it("shows a refused change inline", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockRejectedValue(
			new KubernetesRequestError(
				"Warm workers must not exceed max workers",
				422,
			),
		);
		render(<KubernetesSettings />);

		const input = await screen.findByLabelText("Warm Workers");
		await user.clear(input);
		await user.type(input, "9");
		await user.click(screen.getByRole("button", { name: "Save Scaling" }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Warm workers must not exceed max workers",
		);
	});

	it("shows a field validation error under its field", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockRejectedValue(
			new KubernetesRequestError(
				"Input should be less than or equal to 100",
				422,
				"max_workers",
			),
		);
		render(<KubernetesSettings />);

		const input = await screen.findByLabelText("Max Workers");
		await user.clear(input);
		await user.type(input, "500");
		await user.click(screen.getByRole("button", { name: "Save Scaling" }));

		expect(
			await screen.findByText(
				"Input should be less than or equal to 100",
			),
		).toBeInTheDocument();
		expect(input).toHaveAttribute("aria-invalid", "true");
	});

	it("shows provenance until the API clears it after an edit", async () => {
		const user = userEvent.setup();
		mocks.getKubernetesExecution.mockResolvedValue({
			...settings,
			imported_from_env: {
				max_workers: {
					variable: "BIFROST_POOL_MAX_WORKERS",
					imported_at: "2026-10-24T12:00:00Z",
				},
			},
		});
		mocks.updateKubernetesPolicy.mockResolvedValue({
			...settings,
			policy: { ...policy, max_workers: 8 },
		});
		render(<KubernetesSettings />);

		expect(
			await screen.findByText("BIFROST_POOL_MAX_WORKERS"),
		).toBeInTheDocument();
		expect(screen.getByText(/Imported from/)).toHaveTextContent(
			/Imported from BIFROST_POOL_MAX_WORKERS on Oct 24, 2026/,
		);

		const input = screen.getByLabelText("Max Workers");
		await user.clear(input);
		await user.type(input, "8");
		await user.click(screen.getByRole("button", { name: "Save Scaling" }));

		await waitFor(() =>
			expect(
				screen.queryByText("BIFROST_POOL_MAX_WORKERS"),
			).not.toBeInTheDocument(),
		);
	});

	it("lists variables that are no longer read", async () => {
		mocks.getKubernetesExecution.mockResolvedValue({
			...settings,
			unread_variables: [
				"BIFROST_POOL_MAX_WORKERS",
				"BIFROST_RABBITMQ_URL",
			],
		});
		render(<KubernetesSettings />);

		const notice = (
			await screen.findByText(
				"No longer used; remove from your environment:",
			)
		).closest("[role='status']");
		expect(notice).toHaveTextContent("BIFROST_POOL_MAX_WORKERS");
		expect(notice).toHaveTextContent("BIFROST_RABBITMQ_URL");
		// Narrow screens wrap a name at its underscores, not mid-word.
		const chip = notice?.querySelector("code");
		expect(chip?.textContent).toBe("BIFROST_POOL_MAX_WORKERS");
		expect(chip?.querySelectorAll("wbr")).toHaveLength(3);
	});

	it("hides the notice when every variable is read", async () => {
		render(<KubernetesSettings />);

		await screen.findByLabelText("Max Workers");
		expect(screen.queryByText(/No longer used/)).not.toBeInTheDocument();
	});

	it("turns isolation on", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockResolvedValue({
			...settings,
			policy: { ...policy, isolated_jobs_enabled: true },
		});
		render(<KubernetesSettings />);

		await user.click(await screen.findByLabelText("Isolate Build Jobs"));

		await waitFor(() =>
			expect(mocks.updateKubernetesPolicy).toHaveBeenCalledWith({
				isolated_jobs_enabled: true,
			}),
		);
		expect(screen.getByLabelText("Isolate Build Jobs")).toBeChecked();
	});

	it("saves max isolated pods", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockResolvedValue({
			...settings,
			policy: { ...policy, max_isolated_pods: 4 },
		});
		render(<KubernetesSettings />);

		const input = await screen.findByLabelText("Max Isolated Pods");
		await user.clear(input);
		await user.type(input, "4{Enter}");

		await waitFor(() =>
			expect(mocks.updateKubernetesPolicy).toHaveBeenCalledWith({
				max_isolated_pods: 4,
			}),
		);
	});

	it("refreshes untouched fields from every save and keeps pending edits", async () => {
		const user = userEvent.setup();
		// Another admin changed Worker memory and Max isolated pods meanwhile.
		const changedElsewhere = {
			...policy,
			worker_memory_mib: 4096,
			max_isolated_pods: 5,
		};
		mocks.updateKubernetesPolicy
			.mockResolvedValueOnce({
				...settings,
				policy: { ...changedElsewhere, isolated_jobs_enabled: true },
			})
			.mockResolvedValueOnce({
				...settings,
				policy: {
					...changedElsewhere,
					isolated_jobs_enabled: true,
					max_workers: 10,
				},
			});
		render(<KubernetesSettings />);

		const maxWorkers = await screen.findByLabelText("Max Workers");
		await user.clear(maxWorkers);
		await user.type(maxWorkers, "10");
		await user.click(screen.getByLabelText("Isolate Build Jobs"));

		await waitFor(() =>
			expect(screen.getByLabelText("Worker Memory (MiB)")).toHaveValue(
				4096,
			),
		);
		expect(screen.getByLabelText("Max Isolated Pods")).toHaveValue(5);
		expect(screen.getByLabelText("Max Workers")).toHaveValue(10);

		await user.click(screen.getByRole("button", { name: "Save Scaling" }));

		await waitFor(() =>
			expect(mocks.updateKubernetesPolicy).toHaveBeenLastCalledWith({
				max_workers: 10,
			}),
		);
	});

	it("refreshes the scaling fields when max isolated pods is saved", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesPolicy.mockResolvedValue({
			...settings,
			policy: { ...policy, max_isolated_pods: 4, idle_seconds: 600 },
		});
		render(<KubernetesSettings />);

		const pods = await screen.findByLabelText("Max Isolated Pods");
		await user.clear(pods);
		await user.type(pods, "4{Enter}");

		await waitFor(() =>
			expect(screen.getByLabelText("Idle Time (Seconds)")).toHaveValue(
				600,
			),
		);
		expect(
			screen.getByRole("button", { name: "Save Scaling" }),
		).toBeDisabled();
	});

	it("toggles a job type through the execution endpoint", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesExecution.mockResolvedValue({
			...settings,
			job_types: [
				{ ...settings.job_types[0], enabled: false },
				settings.job_types[1],
			],
		});
		render(<KubernetesSettings />);

		await user.click(await screen.findByLabelText("App Deploys"));

		await waitFor(() =>
			expect(mocks.updateKubernetesExecution).toHaveBeenCalledWith(
				"application.deploy",
				{ enabled: false },
			),
		);
	});

	it("saves a concurrency override from the number input", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesExecution.mockResolvedValue(settings);
		render(<KubernetesSettings />);

		const inputs = await screen.findAllByLabelText("Max Parallel Runs");
		await user.clear(inputs[0]);
		await user.type(inputs[0], "2");
		await user.tab();

		await waitFor(() =>
			expect(mocks.updateKubernetesExecution).toHaveBeenCalledWith(
				"application.deploy",
				{ enabled: true, maxConcurrency: 2 },
			),
		);
	});

	it("shows when a saved Fast Path reservation is not ready", async () => {
		mocks.getKubernetesExecution.mockResolvedValue({
			...settings,
			fast_path_ready_workers: 1,
			policy: { ...policy, reserved_fast_path_workers: 2 },
		});
		render(<KubernetesSettings />);
		expect(
			await screen.findByText(/1 of 2 reserved workers ready/),
		).toHaveTextContent("Reserved capacity is not fully available yet.");
	});

	it("shows the external capacity card with provider bounds", async () => {
		mocks.getWorkerCapacityStatus.mockResolvedValue({
			configured: true,
			endpoint: "https://capacity.example.invalid",
			bounds: {
				protocol_version: 1,
				scope: "test-scope",
				worker_version: "test-build",
				memory_limit_bytes: 4096 * 1024 * 1024,
				max_workers: 2,
				max_lifetime_seconds: 900,
			},
			policy_mismatches: [],
			workers: { ready: 1 },
		});
		render(<KubernetesSettings />);
		expect(
			await screen.findByText("External Workers"),
		).toBeInTheDocument();
		expect(screen.getByText("test-scope")).toBeVisible();
	});

	it("blocks provider-mismatched scaling before saving", async () => {
		const user = userEvent.setup();
		mocks.getWorkerCapacityStatus.mockResolvedValue({
			configured: true,
			bounds: {
				protocol_version: 1,
				scope: "test-scope",
				worker_version: "test-build",
				memory_limit_bytes: 4096 * 1024 * 1024,
				max_workers: 2,
				max_lifetime_seconds: 900,
			},
			policy_mismatches: [],
			workers: {},
		});
		render(<KubernetesSettings />);
		const memory = await screen.findByLabelText("Worker Memory (MiB)");
		await user.clear(memory);
		await user.type(memory, "1024");
		await user.click(screen.getByRole("button", { name: "Save Scaling" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Must be 4096 MiB to match the provider.",
		);
		expect(mocks.updateKubernetesPolicy).not.toHaveBeenCalled();
	});

	it("blocks a Fast Path reservation while external capacity is active", async () => {
		const user = userEvent.setup();
		mocks.getWorkerCapacityStatus.mockResolvedValue({
			configured: true,
			bounds: {
				protocol_version: 1,
				scope: "test-scope",
				worker_version: "test-build",
				memory_limit_bytes: 2048 * 1024 * 1024,
				max_workers: 6,
				max_lifetime_seconds: 900,
			},
			policy_mismatches: [],
			workers: {},
		});
		render(<KubernetesSettings />);
		const reservation = await screen.findByLabelText(
			"Reserved Fast Path Workers",
		);
		await user.clear(reservation);
		await user.type(reservation, "1");
		await user.click(screen.getByRole("button", { name: "Save Scaling" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"External capacity does not support Fast Path reservations.",
		);
		expect(mocks.updateKubernetesPolicy).not.toHaveBeenCalled();
	});
});
