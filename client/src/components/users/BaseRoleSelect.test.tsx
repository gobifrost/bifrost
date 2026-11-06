import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import { USER_ROLE_ID } from "@/lib/builtin-roles";

const baseRoles = vi.fn();
vi.mock("@/hooks/useRoles", () => ({ useBaseRoles: () => baseRoles() }));

import { BaseRoleSelect } from "./BaseRoleSelect";

describe("BaseRoleSelect", () => {
	it("explains and retries a failed role lookup", async () => {
		const refetch = vi.fn();
		baseRoles.mockReturnValue({
			data: [],
			isError: true,
			isLoading: false,
			isFetching: false,
			refetch,
		});
		const { user } = renderWithProviders(
			<BaseRoleSelect value={null} onChange={vi.fn()} />,
		);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Base roles could not be loaded.",
		);
		await user.click(screen.getByRole("button", { name: "Retry Roles" }));
		expect(refetch).toHaveBeenCalledOnce();
	});

	beforeEach(() => {
		baseRoles.mockReturnValue({
			data: [
				{ id: USER_ROLE_ID, name: "User", is_builtin: true },
				{ id: "custom", name: "Support", is_builtin: false },
				{ id: "operator", name: "Operator", is_builtin: true },
			],
			isLoading: false,
			isError: false,
		});
	});

	it("offers the inherited/default choice and only roles eligible as base roles", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<BaseRoleSelect value={null} onChange={onChange} />,
		);

		await user.click(screen.getByRole("combobox", { name: "Base role" }));
		expect(
			screen.getByRole("option", { name: "Use the default base role" }),
		).toBeVisible();
		expect(screen.getByRole("option", { name: "Support" })).toBeVisible();
		expect(screen.queryByRole("option", { name: "Operator" })).toBeNull();

		await user.click(screen.getByRole("option", { name: "Support" }));
		expect(onChange).toHaveBeenCalledWith("custom");
	});
});
