import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

import { CustomClaimsList } from "./CustomClaimsList";
import type { CustomClaim } from "@/services/claims";

const claims: CustomClaim[] = [
	{
		id: "11111111-1111-4111-8111-111111111111",
		organization_id: "22222222-2222-4222-8222-222222222222",
		name: "allowed_campus_ids",
		type: "list",
		description: null,
		query: { table: "user_campus_access", select: "campus_id" },
	},
];

describe("CustomClaimsList", () => {
	it("renders rows and fires onEdit", async () => {
		const onEdit = vi.fn();
		const { user } = renderWithProviders(
			<CustomClaimsList
				claims={claims}
				onEdit={onEdit}
				onDelete={vi.fn()}
				onAdd={vi.fn()}
			/>,
		);

		expect(screen.getByText("allowed_campus_ids")).toBeVisible();
		expect(screen.getByText("user_campus_access")).toBeVisible();

		await user.click(screen.getByRole("button", { name: /edit/i }));

		expect(onEdit).toHaveBeenCalledWith("allowed_campus_ids");
	});

	it("fires onAdd and onDelete", async () => {
		const onAdd = vi.fn();
		const onDelete = vi.fn();
		const { user } = renderWithProviders(
			<CustomClaimsList
				claims={claims}
				onEdit={vi.fn()}
				onDelete={onDelete}
				onAdd={onAdd}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /add claim/i }));
		await user.click(screen.getByRole("button", { name: /delete/i }));

		expect(onAdd).toHaveBeenCalledTimes(1);
		expect(onDelete).toHaveBeenCalledWith("allowed_campus_ids");
	});
});
