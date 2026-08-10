import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	invalidateQueries: vi.fn(),
	useMutation: vi.fn(
		(_method: unknown, _path: unknown, _options: unknown) => ({
			mutateAsync: vi.fn(),
		}),
	),
}));

vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({ invalidateQueries: mocks.invalidateQueries }),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useMutation: mocks.useMutation,
		useQuery: vi.fn(),
	},
}));

import { useUpdateOAuthLoginPreference } from "./oauth-config";

describe("OAuth config service", () => {
	beforeEach(() => {
		mocks.invalidateQueries.mockReset();
		mocks.useMutation.mockClear();
	});

	it("updates the login preference and invalidates both consumers", () => {
		useUpdateOAuthLoginPreference();

		expect(mocks.useMutation).toHaveBeenCalledWith(
			"put",
			"/api/settings/oauth/login-preference",
			expect.objectContaining({ onSuccess: expect.any(Function) }),
		);

		const options = mocks.useMutation.mock.calls[0]?.[2] as {
			onSuccess: () => void;
		};
		options.onSuccess();

		expect(mocks.invalidateQueries).toHaveBeenCalledWith({
			queryKey: ["get", "/api/settings/oauth"],
		});
		expect(mocks.invalidateQueries).toHaveBeenCalledWith({
			queryKey: ["get", "/auth/status"],
		});
	});
});
