import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	getKubernetesExecution: vi.fn(),
	updateKubernetesExecution: vi.fn(),
	updateKubernetesPolicy: vi.fn(),
}));

vi.mock("@/services/kubernetes", async (importOriginal) => ({
	KubernetesRequestError: (
		await importOriginal<typeof import("@/services/kubernetes")>()
	).KubernetesRequestError,
	getKubernetesExecution: (...args: unknown[]) =>
		mocks.getKubernetesExecution(...args),
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
	max_workers: 6,
	worker_memory_mib: 2048,
	largest_worker_mib: 8192,
	idle_seconds: 300,
	isolated_jobs_enabled: false,
	max_isolated_pods: 2,
};

const settings = {
	policy,
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
		mocks.getKubernetesExecution.mockReset();
		mocks.updateKubernetesExecution.mockReset();
		mocks.updateKubernetesPolicy.mockReset();
		mocks.getKubernetesExecution.mockResolvedValue(settings);
	});

	it("shows the scaling and isolation policy", async () => {
		render(<KubernetesSettings />);

		expect(await screen.findByLabelText("Max Workers")).toHaveValue(6);
		expect(screen.getByLabelText("Warm Workers")).toHaveValue(1);
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
});
