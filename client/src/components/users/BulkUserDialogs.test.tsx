/**
 * Component tests for the bulk user dialogs.
 *
 * Covers:
 * - selecting a destination organization and submitting the move dialog
 * - selecting roles and submitting the replace-roles dialog
 * - pending state disables the active toggle submit action
 * - partial failures call the callback with the original users
 * - result dialog renders failed rows and reasons
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

import type { components } from "@/lib/v1";

const mockLookup = vi.fn();
vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => mockLookup(),
}));
vi.mock("@/hooks/useRoles", () => ({ useRoles: () => mockLookup() }));
const mockBulkMutate = vi.fn();
const mockBulkHook = vi.fn();
const mockOrgSelect = vi.fn();
const mockRolesSelect = vi.fn();

vi.mock("@/hooks/useUsers", () => ({
	useBulkUserOperation: () => mockBulkHook(),
}));

const mockCanAt =
	vi.fn<(permission: string, target: { kind: string }) => boolean>();
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ canAt: mockCanAt }),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		id,
		value,
		onChange,
		showGlobal,
		placeholder,
	}: {
		id?: string;
		value: string | null | undefined;
		onChange: (value: string | null | undefined) => void;
		showGlobal?: boolean;
		placeholder?: string;
	}) => {
		mockOrgSelect({ value, showGlobal, placeholder });
		return (
			<label className="block space-y-2">
				<span className="text-sm font-medium">
					Destination organization
				</span>
				<select
					id={id}
					aria-label="Destination organization"
					value={value ?? ""}
					onChange={(e) => {
						const next = e.target.value;
						onChange(
							next === "__global__"
								? null
								: next === ""
									? undefined
									: next,
						);
					}}
				>
					<option value="">
						{placeholder ?? "Select organization..."}
					</option>
					{showGlobal && <option value="__global__">Global</option>}
					<option value="org-1">Acme</option>
					<option value="org-2">Globex</option>
				</select>
			</label>
		);
	},
}));

vi.mock("@/components/forms/RolesMultiSelect", () => ({
	RolesMultiSelect: ({
		value,
		onChange,
		placeholder,
	}: {
		value: string[];
		onChange: (value: string[]) => void;
		placeholder?: string;
	}) => {
		mockRolesSelect({ value, placeholder });
		const toggle = (roleId: string) => {
			const next = new Set(value);
			if (next.has(roleId)) next.delete(roleId);
			else next.add(roleId);
			onChange(Array.from(next));
		};
		return (
			<div aria-label="Roles selector" className="space-y-2">
				<div className="text-sm font-medium">Roles</div>
				<p className="text-xs text-muted-foreground">
					{value.length === 0
						? (placeholder ?? "Select roles...")
						: value.join(", ")}
				</p>
				<div className="flex gap-2">
					<button type="button" onClick={() => toggle("r-1")}>
						Admin
					</button>
					<button type="button" onClick={() => toggle("r-2")}>
						Viewer
					</button>
				</div>
			</div>
		);
	},
}));

import {
	BulkMoveOrgDialog,
	BulkReplaceRolesDialog,
	BulkSetActiveDialog,
	BulkResultDialog,
} from "./BulkUserDialogs";

type User = components["schemas"]["UserPublic"];
type BulkUserResponse = components["schemas"]["BulkUserResponse"];

function makeUser(
	overrides: Partial<NonNullable<User>> = {},
): NonNullable<User> {
	return {
		id: "u-1",
		email: "alice@example.com",
		name: "Alice",
		is_active: true,
		is_superuser: false,
		organization_id: "org-1",
		created_at: "2026-04-20T00:00:00Z",
		updated_at: "2026-04-20T00:00:00Z",
		last_login: null,
		...overrides,
	} as NonNullable<User>;
}

beforeEach(() => {
	mockCanAt.mockReset();
	mockCanAt.mockReturnValue(true);
	mockLookup.mockReturnValue({
		data: [],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	mockBulkMutate.mockReset();
	mockBulkMutate.mockResolvedValue({
		succeeded: ["u-1"],
		failed: [],
	});
	mockBulkHook.mockReset();
	mockBulkHook.mockReturnValue({
		mutateAsync: mockBulkMutate,
		isPending: false,
	});
	mockOrgSelect.mockReset();
	mockRolesSelect.mockReset();
});

describe("BulkMoveOrgDialog", () => {
	it("blocks bulk saves during lookup failures and exposes recovery", async () => {
		const refetch = vi.fn();
		mockLookup.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch,
		});
		const props = {
			open: true,
			onOpenChange: vi.fn(),
			users: [makeUser()],
			onPartialFailure: vi.fn(),
		};
		const { user, rerender } = renderWithProviders(
			<BulkMoveOrgDialog {...props} />,
		);
		expect(
			screen.getByRole("button", { name: /move users/i }),
		).toBeDisabled();
		await user.click(
			screen.getByRole("button", { name: /retry organizations/i }),
		);
		expect(refetch).toHaveBeenCalledOnce();
		rerender(<BulkReplaceRolesDialog {...props} />);
		expect(
			screen.getByRole("button", { name: /^replace roles$/i }),
		).toBeDisabled();
		await user.click(screen.getByRole("button", { name: /retry roles/i }));
		expect(refetch).toHaveBeenCalledTimes(2);
		expect(mockBulkMutate).not.toHaveBeenCalled();
	});

	it("submits the selected organization and closes on success", async () => {
		const onOpenChange = vi.fn();
		const onPartialFailure = vi.fn();
		const onSuccess = vi.fn();
		const { user } = renderWithProviders(
			<BulkMoveOrgDialog
				open={true}
				onOpenChange={onOpenChange}
				users={[
					makeUser(),
					makeUser({ id: "u-2", email: "bob@example.com" }),
				]}
				onPartialFailure={onPartialFailure}
				onSuccess={onSuccess}
			/>,
		);

		await user.selectOptions(
			screen.getByLabelText(/destination organization/i),
			"org-2",
		);
		await user.click(screen.getByRole("button", { name: /move users/i }));

		await waitFor(() => expect(mockBulkMutate).toHaveBeenCalled());
		expect(mockBulkMutate.mock.calls[0]![0]).toEqual({
			body: {
				user_ids: ["u-1", "u-2"],
				operation: "move_org",
				organization_id: "org-2",
			},
		});
		expect(onSuccess).toHaveBeenCalledOnce();
		expect(onPartialFailure).not.toHaveBeenCalled();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("supports moving users to Global", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<BulkMoveOrgDialog
				open={true}
				onOpenChange={onOpenChange}
				users={[makeUser()]}
				onPartialFailure={vi.fn()}
			/>,
		);

		await user.selectOptions(
			screen.getByLabelText(/destination organization/i),
			"__global__",
		);
		await user.click(screen.getByRole("button", { name: /move users/i }));

		await waitFor(() => expect(mockBulkMutate).toHaveBeenCalled());
		expect(
			mockBulkMutate.mock.calls[0]![0].body.organization_id,
		).toBeNull();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("offers Global only to callers who manage user lifecycle platform-wide", () => {
		mockCanAt.mockImplementation(
			(_permission, target) => target.kind !== "global",
		);
		renderWithProviders(
			<BulkMoveOrgDialog
				open={true}
				onOpenChange={vi.fn()}
				users={[makeUser()]}
				onPartialFailure={vi.fn()}
			/>,
		);

		expect(
			screen.queryByRole("option", { name: "Global" }),
		).not.toBeInTheDocument();
	});

	it("reports partial failures without losing the successful rows", async () => {
		const onOpenChange = vi.fn();
		const onPartialFailure = vi.fn();
		const onSuccess = vi.fn();
		mockBulkMutate.mockResolvedValueOnce({
			succeeded: ["u-1"],
			failed: [{ user_id: "u-2", reason: "Already belongs to the org" }],
		});

		const { user } = renderWithProviders(
			<BulkMoveOrgDialog
				open={true}
				onOpenChange={onOpenChange}
				users={[
					makeUser(),
					makeUser({ id: "u-2", email: "bob@example.com" }),
				]}
				onPartialFailure={onPartialFailure}
				onSuccess={onSuccess}
			/>,
		);

		await user.selectOptions(
			screen.getByLabelText(/destination organization/i),
			"org-2",
		);
		await user.click(screen.getByRole("button", { name: /move users/i }));

		await waitFor(() => expect(onPartialFailure).toHaveBeenCalledOnce());
		expect(onPartialFailure.mock.calls[0]![0]).toEqual({
			succeeded: ["u-1"],
			failed: [{ user_id: "u-2", reason: "Already belongs to the org" }],
		});
		expect(onPartialFailure.mock.calls[0]![1]).toEqual([
			makeUser(),
			makeUser({ id: "u-2", email: "bob@example.com" }),
		]);
		expect(onSuccess).toHaveBeenCalledOnce();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("keeps the dialog open and surfaces an inline error when the move fails", async () => {
		const onOpenChange = vi.fn();
		mockBulkMutate.mockRejectedValueOnce(
			new Error("Synthetic bulk operation failed"),
		);
		const { user } = renderWithProviders(
			<BulkMoveOrgDialog
				open={true}
				onOpenChange={onOpenChange}
				users={[makeUser()]}
				onPartialFailure={vi.fn()}
			/>,
		);

		await user.selectOptions(
			screen.getByLabelText(/destination organization/i),
			"org-2",
		);
		await user.click(screen.getByRole("button", { name: /move users/i }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			/Synthetic bulk operation failed/i,
		);
		expect(onOpenChange).not.toHaveBeenCalledWith(false);
		expect(
			screen.getByRole("button", { name: /move users/i }),
		).toBeEnabled();
	});
});

describe("BulkReplaceRolesDialog", () => {
	it("submits the selected roles and closes on success", async () => {
		const onOpenChange = vi.fn();
		const onPartialFailure = vi.fn();
		const onSuccess = vi.fn();
		const { user } = renderWithProviders(
			<BulkReplaceRolesDialog
				open={true}
				onOpenChange={onOpenChange}
				users={[makeUser()]}
				onPartialFailure={onPartialFailure}
				onSuccess={onSuccess}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /admin/i }));
		await user.click(screen.getByRole("button", { name: /viewer/i }));
		await user.click(
			screen.getByRole("button", { name: /replace roles/i }),
		);

		await waitFor(() => expect(mockBulkMutate).toHaveBeenCalled());
		expect(mockBulkMutate.mock.calls[0]![0]).toEqual({
			body: {
				user_ids: ["u-1"],
				operation: "replace_roles",
				role_ids: ["r-1", "r-2"],
			},
		});
		expect(onSuccess).toHaveBeenCalledOnce();
		expect(onPartialFailure).not.toHaveBeenCalled();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("reports partial failures without losing the successful role updates", async () => {
		const onOpenChange = vi.fn();
		const onPartialFailure = vi.fn();
		const onSuccess = vi.fn();
		mockBulkMutate.mockResolvedValueOnce({
			succeeded: ["u-1"],
			failed: [
				{ user_id: "u-2", reason: "Role already assigned elsewhere" },
			],
		});

		const { user } = renderWithProviders(
			<BulkReplaceRolesDialog
				open={true}
				onOpenChange={onOpenChange}
				users={[
					makeUser(),
					makeUser({ id: "u-2", email: "bob@example.com" }),
				]}
				onPartialFailure={onPartialFailure}
				onSuccess={onSuccess}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /admin/i }));
		await user.click(
			screen.getByRole("button", { name: /replace roles/i }),
		);

		await waitFor(() => expect(onPartialFailure).toHaveBeenCalledOnce());
		expect(onPartialFailure.mock.calls[0]![0]).toEqual({
			succeeded: ["u-1"],
			failed: [
				{ user_id: "u-2", reason: "Role already assigned elsewhere" },
			],
		});
		expect(onPartialFailure.mock.calls[0]![1]).toEqual([
			makeUser(),
			makeUser({ id: "u-2", email: "bob@example.com" }),
		]);
		expect(onSuccess).toHaveBeenCalledOnce();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});
});

describe("BulkSetActiveDialog", () => {
	it("disables submit while the mutation is pending", () => {
		mockBulkHook.mockReturnValueOnce({
			mutateAsync: mockBulkMutate,
			isPending: true,
		});

		renderWithProviders(
			<BulkSetActiveDialog
				open={true}
				onOpenChange={vi.fn()}
				users={[makeUser()]}
				mode="disable"
				onPartialFailure={vi.fn()}
			/>,
		);

		expect(
			screen.getByRole("button", { name: /disabling\.\.\./i }),
		).toBeDisabled();
	});

	it("reports partial failures without losing the successful active-state updates", async () => {
		const onOpenChange = vi.fn();
		const onPartialFailure = vi.fn();
		const onSuccess = vi.fn();
		mockBulkMutate.mockResolvedValueOnce({
			succeeded: ["u-1"],
			failed: [{ user_id: "u-2", reason: "Already disabled elsewhere" }],
		});

		const { user } = renderWithProviders(
			<BulkSetActiveDialog
				open={true}
				onOpenChange={onOpenChange}
				users={[
					makeUser(),
					makeUser({ id: "u-2", email: "bob@example.com" }),
				]}
				mode="disable"
				onPartialFailure={onPartialFailure}
				onSuccess={onSuccess}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /disable users/i }),
		);

		await waitFor(() => expect(onPartialFailure).toHaveBeenCalledOnce());
		expect(onPartialFailure.mock.calls[0]![0]).toEqual({
			succeeded: ["u-1"],
			failed: [{ user_id: "u-2", reason: "Already disabled elsewhere" }],
		});
		expect(onPartialFailure.mock.calls[0]![1]).toEqual([
			makeUser(),
			makeUser({ id: "u-2", email: "bob@example.com" }),
		]);
		expect(onSuccess).toHaveBeenCalledOnce();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});
});

describe("BulkResultDialog", () => {
	it("renders partial failure rows and reasons", () => {
		const result: BulkUserResponse = {
			succeeded: ["u-1"],
			failed: [
				{ user_id: "u-2", reason: "Already assigned elsewhere" },
				{ user_id: "u-3", reason: "Permission denied" },
			],
		};

		renderWithProviders(
			<BulkResultDialog
				open={true}
				onOpenChange={vi.fn()}
				result={result}
				users={[
					makeUser(),
					makeUser({
						id: "u-2",
						email: "bob@example.com",
						name: "Bob",
					}),
					makeUser({
						id: "u-3",
						email: "cara@example.com",
						name: "Cara",
					}),
				]}
			/>,
		);

		expect(
			screen.getByRole("heading", { name: /bulk action results/i }),
		).toBeInTheDocument();
		expect(screen.getByText(/1 succeeded · 2 failed/i)).toBeInTheDocument();
		expect(screen.getByText("Bob")).toBeInTheDocument();
		expect(
			screen.getByText("Already assigned elsewhere"),
		).toBeInTheDocument();
		expect(screen.getByText("Cara")).toBeInTheDocument();
		expect(screen.getByText("Permission denied")).toBeInTheDocument();
	});
});
