import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	getCloudflareCapacity: vi.fn(),
	connectCloudflareCapacity: vi.fn(),
	updateCloudflareCapacityRules: vi.fn(),
	onPlatformJobUpdate: vi.fn(),
	observePlatformJob: vi.fn(),
}));

vi.mock("@/services/workerCapacity", () => ({
	getCloudflareCapacity: (...args: unknown[]) =>
		mocks.getCloudflareCapacity(...args),
	connectCloudflareCapacity: (...args: unknown[]) =>
		mocks.connectCloudflareCapacity(...args),
	updateCloudflareCapacityRules: (...args: unknown[]) =>
		mocks.updateCloudflareCapacityRules(...args),
}));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		onPlatformJobUpdate: (...args: unknown[]) =>
			mocks.onPlatformJobUpdate(...args),
	},
}));
vi.mock("@/services/platformJobs", () => ({
	observePlatformJob: (...args: unknown[]) =>
		mocks.observePlatformJob(...args),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { CloudflareCapacitySettings } from "./CloudflareCapacitySettings";

const unconfigured = {
	configured: false,
	active: false,
	status: "unconfigured" as const,
	accounts: [],
};

function renderSettings() {
	return render(<CloudflareCapacitySettings />);
}

describe("CloudflareCapacitySettings", () => {
	beforeEach(() => {
		mocks.getCloudflareCapacity.mockReset();
		mocks.connectCloudflareCapacity.mockReset();
		mocks.updateCloudflareCapacityRules.mockReset();
		mocks.onPlatformJobUpdate.mockReset();
		mocks.observePlatformJob.mockReset();
		mocks.onPlatformJobUpdate.mockReturnValue(vi.fn());
		mocks.observePlatformJob.mockReturnValue({
			promise: Promise.resolve(undefined),
			cancel: vi.fn(),
		});
		mocks.getCloudflareCapacity.mockResolvedValue(unconfigured);
	});

	it("shows loading before the connection status is available", () => {
		mocks.getCloudflareCapacity.mockReturnValue(new Promise(() => undefined));
		renderSettings();
		expect(screen.getByRole("status")).toHaveTextContent(
			"Loading Cloudflare capacity…",
		);
	});

	it("guides an unconfigured operator to enter a write-only API key", async () => {
		renderSettings();
		expect(await screen.findByText("Not Connected")).toBeVisible();
		expect(screen.getByLabelText("Cloudflare API Key")).toHaveAttribute(
			"type",
			"password",
		);
		expect(screen.getByLabelText("Max Cloudflare Workers")).toHaveValue(2);
		expect(
			screen.getByRole("button", { name: "Connect Cloudflare" }),
		).toBeDisabled();
		expect(
			screen.getByRole("switch", { name: "Run Agents and Summaries" }),
		).toBeDisabled();
	});

	it("clears the write-only API key after a setup attempt fails", async () => {
		const user = userEvent.setup();
		mocks.connectCloudflareCapacity.mockRejectedValue(
			new Error("Cloudflare could not verify the API key."),
		);
		renderSettings();
		const key = await screen.findByLabelText("Cloudflare API Key");
		await user.type(key, "write-only-secret");
		await user.type(
			screen.getByLabelText("Bifrost Public API URL"),
			"https://bifrost.example.com",
		);
		await user.click(screen.getByRole("button", { name: "Connect Cloudflare" }));

		await waitFor(() => expect(mocks.connectCloudflareCapacity).toHaveBeenCalledWith(
			expect.objectContaining({ api_key: "write-only-secret", max_workers: 2 }),
		));
		await waitFor(() => expect(key).toHaveValue(""));
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Cloudflare could not verify the API key.",
		);
	});

	it("shows an account picker only when Cloudflare requires a selection", async () => {
		const user = userEvent.setup();
		mocks.getCloudflareCapacity.mockResolvedValue({
			configured: true,
			active: false,
			status: "account_selection_required" as const,
			accounts: [
				{ id: "a".repeat(32), name: "Northwind" },
				{ id: "b".repeat(32), name: "Contoso" },
			],
		});
		mocks.connectCloudflareCapacity.mockResolvedValue({
			job_id: "job-2",
			connection: { ...unconfigured, configured: true, status: "provisioning" },
		});
		renderSettings();
		await screen.findByLabelText("Cloudflare Account");
		await user.click(screen.getByLabelText("Cloudflare Account"));
		await user.click(screen.getByRole("option", { name: "Contoso" }));
		await user.click(screen.getByRole("button", { name: "Continue Setup" }));

		await waitFor(() =>
			expect(mocks.connectCloudflareCapacity).toHaveBeenCalledWith({
				account_id: "b".repeat(32),
				max_workers: 2,
			}),
		);
	});

	it("offers retry after a failed setup and reloads on demand", async () => {
		const user = userEvent.setup();
		mocks.getCloudflareCapacity.mockResolvedValueOnce({
			configured: true,
			active: false,
			status: "failed" as const,
			accounts: [],
			error: "Cloudflare could not verify the provider.",
		});
		mocks.getCloudflareCapacity.mockResolvedValueOnce(unconfigured);
		renderSettings();
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Cloudflare could not verify the provider.",
		);
		expect(screen.getByRole("button", { name: "Retry Setup" })).toBeEnabled();
		await user.click(screen.getByRole("button", { name: "Reload Status" }));
		await waitFor(() => expect(screen.getByText("Not Connected")).toBeVisible());
	});

	it("saves configured Cloudflare start rules without asking for the API key", async () => {
		const user = userEvent.setup();
		const configured = {
			configured: true,
			active: true,
			status: "ready" as const,
			accounts: [],
			rules: {
				workflows: true,
				agents: true,
				app_builds: true,
				prefer_kubernetes: false,
			},
		};
		mocks.getCloudflareCapacity.mockResolvedValue(configured);
		mocks.updateCloudflareCapacityRules.mockResolvedValue({
			...configured,
			rules: { ...configured.rules, agents: false },
		});
		renderSettings();
		const agents = await screen.findByRole("switch", {
			name: "Run Agents and Summaries",
		});
		await user.click(agents);
		await user.click(screen.getByRole("button", { name: "Save Cloudflare Rules" }));

		await waitFor(() =>
			expect(mocks.updateCloudflareCapacityRules).toHaveBeenCalledWith({
				workflows: true,
				agents: false,
				app_builds: true,
				prefer_kubernetes: false,
			}),
		);
		expect(screen.queryByLabelText("Replace Cloudflare API Key")).toBeInTheDocument();
	});

	it("keeps a changed rule visible when saving it is refused", async () => {
		const user = userEvent.setup();
		mocks.getCloudflareCapacity.mockResolvedValue({
			configured: true,
			active: true,
			status: "ready" as const,
			accounts: [],
			rules: {
				workflows: true,
				agents: true,
				app_builds: true,
				prefer_kubernetes: false,
			},
		});
		mocks.updateCloudflareCapacityRules.mockRejectedValue(
			new Error("Cloudflare start rules could not be saved."),
		);
		renderSettings();
		const kubernetes = await screen.findByRole("switch", {
			name: "Let Kubernetes Scale First",
		});
		await user.click(kubernetes);
		await user.click(screen.getByRole("button", { name: "Save Cloudflare Rules" }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Cloudflare start rules could not be saved.",
		);
		expect(kubernetes).toBeChecked();
	});
});
