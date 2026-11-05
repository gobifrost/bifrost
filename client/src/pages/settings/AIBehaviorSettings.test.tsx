import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mutateAsync = vi.fn();
const refetch = vi.fn().mockResolvedValue({ error: null });
let behaviorData:
	| {
			default_system_prompt: string | null;
			agent_execution_mode: "delegated" | "automatic";
	  }
	| undefined = {
	default_system_prompt: "Be helpful.",
	agent_execution_mode: "delegated",
};
let queryError = false;

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: () => ({
			data: behaviorData,
			isLoading: false,
			isError: queryError,
			isFetching: false,
			refetch,
		}),
		useMutation: () => ({ mutateAsync, isPending: false }),
	},
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AIBehaviorSettings } from "./AIBehaviorSettings";

beforeEach(() => {
	behaviorData = {
		default_system_prompt: "Be helpful.",
		agent_execution_mode: "delegated",
	};
	queryError = false;
	mutateAsync
		.mockReset()
		.mockImplementation(
			async ({
				body,
			}: {
				body: Record<string, string | null | undefined>;
			}) => ({
				default_system_prompt:
					body.default_system_prompt ??
					behaviorData?.default_system_prompt ??
					null,
				agent_execution_mode:
					body.agent_execution_mode ??
					behaviorData?.agent_execution_mode ??
					"delegated",
			}),
		);
	refetch.mockClear();
});

describe("AIBehaviorSettings", () => {
	it("loads the delegated default and saves automatic execution independently", async () => {
		const user = userEvent.setup();
		render(<AIBehaviorSettings />);

		const toggle = screen.getByRole("switch", {
			name: "Automatic Agent Execution",
		});
		expect(toggle).not.toBeChecked();
		await user.click(toggle);

		await waitFor(() =>
			expect(mutateAsync).toHaveBeenCalledWith({
				body: { agent_execution_mode: "automatic" },
			}),
		);
	});

	it("rolls the toggle back when saving execution mode fails", async () => {
		mutateAsync.mockRejectedValueOnce(new Error("Synthetic failure"));
		const user = userEvent.setup();
		const { rerender } = render(<AIBehaviorSettings />);

		const toggle = screen.getByRole("switch", {
			name: "Automatic Agent Execution",
		});
		await user.click(toggle);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not confirm the setting was saved",
		);
		expect(toggle).not.toBeChecked();
		behaviorData = {
			default_system_prompt: "Be helpful.",
			agent_execution_mode: "automatic",
		};
		rerender(<AIBehaviorSettings />);
		expect(toggle).not.toBeChecked();
		await user.click(screen.getByRole("button", { name: "Reload Setting" }));
		expect(refetch).toHaveBeenCalled();
		await waitFor(() => expect(toggle).toBeChecked());
	});

	it("reflects server changes after saving execution mode", async () => {
		const user = userEvent.setup();
		const { rerender } = render(<AIBehaviorSettings />);
		const toggle = screen.getByRole("switch", {
			name: "Automatic Agent Execution",
		});

		await user.click(toggle);
		await waitFor(() =>
			expect(mutateAsync).toHaveBeenCalledWith({
				body: { agent_execution_mode: "automatic" },
			}),
		);
		behaviorData = {
			default_system_prompt: "Be helpful.",
			agent_execution_mode: "automatic",
		};
		rerender(<AIBehaviorSettings />);
		await waitFor(() => expect(toggle).toBeChecked());

		behaviorData = {
			default_system_prompt: "Be helpful.",
			agent_execution_mode: "delegated",
		};
		rerender(<AIBehaviorSettings />);
		await waitFor(() => expect(toggle).not.toBeChecked());
	});

	it("keeps a chat-instructions draft when execution mode changes", async () => {
		const user = userEvent.setup();
		render(<AIBehaviorSettings />);

		const instructions = screen.getByLabelText("Instructions");
		await user.clear(instructions);
		await user.type(instructions, "Keep this draft");
		await user.click(
			screen.getByRole("switch", { name: "Automatic Agent Execution" }),
		);

		await waitFor(() =>
			expect(mutateAsync).toHaveBeenCalledWith({
				body: { agent_execution_mode: "automatic" },
			}),
		);
		expect(instructions).toHaveValue("Keep this draft");
	});

	it("saves agentless Chat instructions", async () => {
		const user = userEvent.setup();
		render(<AIBehaviorSettings />);

		const instructions = screen.getByLabelText("Instructions");
		await user.clear(instructions);
		await user.type(instructions, "Be direct.");
		await user.click(
			screen.getByRole("button", { name: "Save instructions" }),
		);

		await waitFor(() =>
			expect(mutateAsync).toHaveBeenCalledWith({
				body: { default_system_prompt: "Be direct." },
			}),
		);
	});

	it("shows a retryable error when behavior settings cannot load", () => {
		behaviorData = undefined;
		queryError = true;
		render(<AIBehaviorSettings />);

		expect(screen.getByRole("alert")).toHaveTextContent(
			"Couldn't load AI behavior",
		);
		expect(
			screen.getByRole("switch", { name: "Automatic Agent Execution" }),
		).toBeDisabled();
	});
});

it("keeps unsaved edits when background data changes", async () => {
	const user = userEvent.setup();
	const { rerender } = render(<AIBehaviorSettings />);
	const input = screen.getByLabelText("Instructions");
	await user.clear(input);
	await user.type(input, "Keep my draft");
	behaviorData = {
		default_system_prompt: "Background update",
		agent_execution_mode: "delegated",
	};
	rerender(<AIBehaviorSettings />);
	expect(input).toHaveValue("Keep my draft");
});

it("retains a failed save and lets the user retry", async () => {
	mutateAsync.mockRejectedValueOnce(new Error("Synthetic failure"));
	const user = userEvent.setup();
	render(<AIBehaviorSettings />);
	await user.clear(screen.getByLabelText("Instructions"));
	await user.type(screen.getByLabelText("Instructions"), "Retry this draft");
	await user.click(screen.getByRole("button", { name: "Save instructions" }));
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Your draft is preserved",
	);
	expect(screen.getByLabelText("Instructions")).toHaveValue(
		"Retry this draft",
	);
	await user.click(screen.getByRole("button", { name: "Save instructions" }));
	await waitFor(() =>
		expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
	);
});
