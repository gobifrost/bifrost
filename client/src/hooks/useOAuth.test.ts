import { renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const { useMutation, invalidate, toastSuccess, toastError } = vi.hoisted(() => ({
	useMutation: vi.fn(),
	invalidate: vi.fn(),
	toastSuccess: vi.fn(),
	toastError: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({ $api: { useMutation }, apiClient: {} }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ invalidateQueries: invalidate }) }));
vi.mock("sonner", () => ({ toast: { success: toastSuccess, error: toastError } }));
import { useRefreshOAuthToken } from "./useOAuth";

type RefreshOptions = {
	onSuccess: (data: { success: boolean; message: string }, variables: unknown) => void;
};

function refreshOptions(): RefreshOptions {
	renderHook(() => useRefreshOAuthToken());
	return useMutation.mock.calls[0][2] as RefreshOptions;
}

const variables = { params: { path: { connection_name: "printix" } } };

beforeEach(() => vi.clearAllMocks());

it("reports a 200 response with success=false as a refresh failure", () => {
	refreshOptions().onSuccess(
		{ success: false, message: "Token refresh failed: certificate verify failed" },
		variables,
	);
	expect(toastError).toHaveBeenCalledWith("Token refresh failed: certificate verify failed");
	expect(toastSuccess).not.toHaveBeenCalled();
	expect(invalidate).toHaveBeenCalledTimes(2);
});

it("reports success only when the response says the refresh succeeded", () => {
	refreshOptions().onSuccess({ success: true, message: "Token refreshed" }, variables);
	expect(toastSuccess).toHaveBeenCalledWith("OAuth token refreshed successfully");
	expect(toastError).not.toHaveBeenCalled();
	expect(invalidate).toHaveBeenCalledTimes(2);
});
