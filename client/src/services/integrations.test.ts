/**
 * Tests for per-mapping OAuth service hooks.
 *
 * Scope: `useAuthorizeMapping` and `useDisconnectMapping`.
 * Verifies that each hook passes the correct route string to `$api.useMutation`
 * and that the `onSuccess` callback invalidates the right query key.
 *
 * The other hooks in integrations.ts are exercised through their consumer
 * components and are out of scope for this test file.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// Capture the last useMutation call so we can inspect the route and options.
const mockUseMutation = vi.fn();
const mockInvalidateQueries = vi.fn();

vi.mock("@/lib/api-client", () => ({
	$api: {
		useMutation: (...args: unknown[]) => mockUseMutation(...args),
	},
	apiClient: { POST: vi.fn() },
}));

vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}));

// Import after mocks are in place.
import { useAuthorizeMapping, useDisconnectMapping } from "./integrations";

beforeEach(() => {
	mockUseMutation.mockReset();
	mockInvalidateQueries.mockReset();
	// Return a stable object so hooks don't throw on destructuring.
	mockUseMutation.mockReturnValue({ mutate: vi.fn(), isPending: false });
});

describe("useAuthorizeMapping", () => {
	it("calls useMutation with the per-mapping authorize route", () => {
		useAuthorizeMapping();
		expect(mockUseMutation).toHaveBeenCalledTimes(1);
		const [method, route] = mockUseMutation.mock.calls[0]!;
		expect(method).toBe("post");
		expect(route).toBe(
			"/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/authorize",
		);
	});

	it("onSuccess invalidates the integration detail query", () => {
		useAuthorizeMapping();
		const [, , options] = mockUseMutation.mock.calls[0]!;
		const fakeVariables = {
			params: { path: { integration_id: "integ-1", mapping_id: "map-1" } },
		};
		options.onSuccess(undefined, fakeVariables, undefined);
		expect(mockInvalidateQueries).toHaveBeenCalledWith({
			queryKey: [
				"get",
				"/api/integrations/{integration_id}",
				{ params: { path: { integration_id: "integ-1" } } },
			],
		});
	});
});

describe("useDisconnectMapping", () => {
	it("calls useMutation with the per-mapping disconnect route", () => {
		useDisconnectMapping();
		expect(mockUseMutation).toHaveBeenCalledTimes(1);
		const [method, route] = mockUseMutation.mock.calls[0]!;
		expect(method).toBe("post");
		expect(route).toBe(
			"/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/disconnect",
		);
	});

	it("onSuccess invalidates the integration detail query", () => {
		useDisconnectMapping();
		const [, , options] = mockUseMutation.mock.calls[0]!;
		const fakeVariables = {
			params: { path: { integration_id: "integ-2", mapping_id: "map-2" } },
		};
		options.onSuccess(undefined, fakeVariables, undefined);
		expect(mockInvalidateQueries).toHaveBeenCalledWith({
			queryKey: [
				"get",
				"/api/integrations/{integration_id}",
				{ params: { path: { integration_id: "integ-2" } } },
			],
		});
	});
});
