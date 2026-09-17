import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	getKubernetesExecution: vi.fn(),
	updateKubernetesExecution: vi.fn(),
}));

vi.mock("@/services/kubernetes", () => ({
	getKubernetesExecution: (...args: unknown[]) =>
		mocks.getKubernetesExecution(...args),
	updateKubernetesExecution: (...args: unknown[]) =>
		mocks.updateKubernetesExecution(...args),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { KubernetesExecutions } from "./KubernetesExecutions";

const settings = {
	job_types: [
		{
			job_type: "application.deploy",
			title: "App deploys",
			description: "Compile apps in pods.",
			enabled: true,
			default_enabled: true,
			allowed_by_deployment: true,
		},
		{
			job_type: "application.sdk_update",
			title: "App SDK rebuilds",
			description: "Rebuild the SDK in pods.",
			enabled: false,
			default_enabled: true,
			allowed_by_deployment: false,
		},
	],
};

describe("KubernetesExecutions", () => {
	beforeEach(() => {
		mocks.getKubernetesExecution.mockReset();
		mocks.updateKubernetesExecution.mockReset();
		mocks.getKubernetesExecution.mockResolvedValue(settings);
	});

	it("lists one toggle per eligible job type", async () => {
		render(<KubernetesExecutions />);

		expect(await screen.findByText("App deploys")).toBeInTheDocument();
		expect(screen.getByText("App SDK rebuilds")).toBeInTheDocument();
	});

	it("explains when the deployment allowlist blocks a type", async () => {
		render(<KubernetesExecutions />);

		expect(
			await screen.findByText(/blocked by the deployment allowlist/),
		).toBeInTheDocument();
	});

	it("toggles a type through the shared endpoint", async () => {
		const user = userEvent.setup();
		mocks.updateKubernetesExecution.mockResolvedValue({
			job_types: [
				{ ...settings.job_types[0], enabled: false },
				settings.job_types[1],
			],
		});
		render(<KubernetesExecutions />);

		await user.click(await screen.findByLabelText("App deploys"));

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
		render(<KubernetesExecutions />);

		const inputs = await screen.findAllByLabelText("Max parallel runs");
		const input = inputs[0];
		await user.clear(input);
		await user.type(input, "2");
		await user.tab();

		await waitFor(() =>
			expect(mocks.updateKubernetesExecution).toHaveBeenCalledWith(
				"application.deploy",
				{ enabled: true, maxConcurrency: 2 },
			),
		);
	});
});
