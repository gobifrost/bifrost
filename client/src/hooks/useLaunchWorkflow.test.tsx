import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { components } from "@/lib/v1";

const state = vi.hoisted(() => ({
	context: { query: { reference: "review" } },
	setWorkflowResults: vi.fn(),
	setStartupHandle: vi.fn(),
	setIsLoadingLaunchWorkflow: vi.fn(),
}));
const execute = vi.hoisted(() => vi.fn());
vi.mock("@/contexts/FormContext", () => ({ useFormContext: () => state }));
vi.mock("@/hooks/useForms", () => ({ executeFormStartup: execute }));
import { useLaunchWorkflow } from "./useLaunchWorkflow";
const form = {
	id: "form-review",
	has_startup: true,
} as components["schemas"]["FormRuntimeDefinition"];
beforeEach(() => {
	vi.clearAllMocks();
});

it("exposes startup failure and retries with the same permitted inputs", async () => {
	execute
		.mockRejectedValueOnce(new Error("Service unavailable"))
		.mockResolvedValueOnce({
			result: { ready: true },
			startup_handle: "synthetic-handle",
		});
	const { result } = renderHook(() => useLaunchWorkflow({ form }));
	await waitFor(() =>
		expect(result.current.error).toBe("Service unavailable"),
	);
	expect(state.setStartupHandle).toHaveBeenLastCalledWith(null);
	act(() => result.current.retry());
	await waitFor(() =>
		expect(state.setStartupHandle).toHaveBeenLastCalledWith(
			"synthetic-handle",
		),
	);
	expect(result.current.error).toBeNull();
	expect(execute).toHaveBeenCalledTimes(2);
	expect(execute.mock.calls[1]).toEqual(execute.mock.calls[0]);
	expect(state.setWorkflowResults).toHaveBeenLastCalledWith({ ready: true });
	expect(state.setIsLoadingLaunchWorkflow).toHaveBeenLastCalledWith(false);
});

it("ignores a previous form's late startup response", async () => {
	let resolveOld!: (value: unknown) => void;
	execute
		.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					resolveOld = resolve;
				}),
		)
		.mockResolvedValueOnce({
			result: { current: true },
			startup_handle: "current-handle",
		});
	const { rerender } = renderHook(
		({ id }) => useLaunchWorkflow({ form: { ...form, id } }),
		{ initialProps: { id: "old-form" } },
	);
	rerender({ id: "new-form" });
	await waitFor(() =>
		expect(state.setStartupHandle).toHaveBeenLastCalledWith(
			"current-handle",
		),
	);
	await act(async () =>
		resolveOld({ result: { stale: true }, startup_handle: "old-handle" }),
	);
	expect(state.setStartupHandle).toHaveBeenLastCalledWith("current-handle");
	expect(state.setWorkflowResults).not.toHaveBeenCalledWith({ stale: true });
});

it("clears pending loading when the next form has no startup", async () => {
	let finish!: (value: unknown) => void;
	execute.mockImplementationOnce(
		() =>
			new Promise((resolve) => {
				finish = resolve;
			}),
	);
	const { rerender, result } = renderHook(
		({ has_startup }) =>
			useLaunchWorkflow({ form: { ...form, has_startup } }),
		{ initialProps: { has_startup: true } },
	);
	rerender({ has_startup: false });
	expect(state.setIsLoadingLaunchWorkflow).toHaveBeenLastCalledWith(false);
	expect(result.current.error).toBeNull();
	await act(async () => finish({ result: {}, startup_handle: "stale" }));
	expect(state.setStartupHandle).not.toHaveBeenCalledWith("stale");
});

it("reloads startup in the new organization and ignores the previous organization's late result", async () => {
	let finishOld!: (value: unknown) => void;
	execute
		.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					finishOld = resolve;
				}),
		)
		.mockResolvedValueOnce({
			result: { customer: "new" },
			startup_handle: "new-handle",
		});
	const { rerender } = renderHook(
		({ orgId }) => useLaunchWorkflow({ form, orgId }),
		{ initialProps: { orgId: "old" } },
	);
	rerender({ orgId: "new" });
	await waitFor(() =>
		expect(state.setStartupHandle).toHaveBeenLastCalledWith("new-handle"),
	);
	await act(async () =>
		finishOld({
			result: { customer: "old" },
			startup_handle: "old-handle",
		}),
	);
	expect(execute).toHaveBeenLastCalledWith(
		form.id,
		state.context.query,
		"new",
	);
	expect(state.setWorkflowResults).toHaveBeenLastCalledWith({
		customer: "new",
	});
});
