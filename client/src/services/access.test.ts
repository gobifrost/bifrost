import createClient from "openapi-fetch";
import createQueryClient from "openapi-react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { paths } from "@/lib/v1";

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: (...args: unknown[]) => useQueryMock(...args) },
}));

import {
	USER_ACCESS_QUERY_KEY,
	usePermissionCatalog,
	useUserAccessMap,
} from "./access";

describe("access service", () => {
	beforeEach(() => {
		useQueryMock.mockReset();
	});

	it("reads the permission catalog from its endpoint", () => {
		usePermissionCatalog();

		expect(useQueryMock).toHaveBeenCalledWith(
			"get",
			"/api/permissions/catalog",
		);
	});

	it("reads a person's access map from their path", () => {
		useUserAccessMap("user-1");

		expect(useQueryMock).toHaveBeenCalledWith(
			"get",
			"/api/users/{user_id}/access",
			{ params: { path: { user_id: "user-1" } } },
			{ enabled: true },
		);
	});

	it("does not request without a user", () => {
		useUserAccessMap(undefined);

		expect(useQueryMock.mock.calls[0][3]).toEqual({ enabled: false });
	});

	it("keys the map under the invalidation prefix", () => {
		const api = createQueryClient(createClient<paths>());

		const { queryKey } = api.queryOptions(
			"get",
			"/api/users/{user_id}/access",
			{
				params: { path: { user_id: "user-1" } },
			},
		);

		expect(queryKey.slice(0, USER_ACCESS_QUERY_KEY.length)).toEqual([
			...USER_ACCESS_QUERY_KEY,
		]);
	});
});
