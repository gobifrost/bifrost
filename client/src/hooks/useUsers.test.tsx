import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

const useQuery = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api-client", () => ({
	$api: { useQuery },
}));

import { useUsersFiltered } from "./useUsers";

describe("useUsersFiltered", () => {
	it("passes enabled=false through to the generated query hook", () => {
		useQuery.mockReturnValue({ data: [] });

		renderHook(() =>
			useUsersFiltered("org-1", false, "organization:org-1", false),
		);

		expect(useQuery).toHaveBeenCalledWith(
			"get",
			"/api/users",
			{
				headers: { "X-Bifrost-Boundary": "organization:org-1" },
				params: { query: { scope: "org-1" } },
			},
			{ enabled: false },
		);
	});
});
