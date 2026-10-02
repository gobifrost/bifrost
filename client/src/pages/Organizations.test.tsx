import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockUseOrganizations, mockUpdate } = vi.hoisted(() => ({
	mockUseOrganizations: vi.fn(),
	mockUpdate: vi.fn(),
}));
const mockCreate = vi.fn();
const mockUseMediaQuery = vi.fn(() => false);
let nextCreateError: Error | undefined;
let nextUpdateError: Error | undefined;

const organizations = [
	{
		id: "org-1",
		name: "Acme",
		domain: "acme.example",
		is_active: true,
		is_provider: false,
		settings: {},
		created_at: "2026-08-13T00:00:00Z",
		updated_at: "2026-08-13T00:00:00Z",
		created_by: "admin@example.com",
	},
	{
		id: "org-2",
		name: "Dormant Co",
		domain: "dormant.example",
		is_active: false,
		is_provider: false,
		settings: {},
		created_at: "2026-08-12T00:00:00Z",
		updated_at: "2026-08-12T00:00:00Z",
		created_by: "admin@example.com",
	},
];
const manyOrganizations = Array.from({ length: 30 }, (_, index) => ({
	id: `org-${index + 1}`,
	name: `Organization ${index + 1}`,
	domain: `org-${index + 1}.example`,
	is_active: true,
	is_provider: false,
	settings: {},
	created_at: "2026-08-13T00:00:00Z",
	updated_at: "2026-08-13T00:00:00Z",
	created_by: "admin@example.com",
}));
let organizationRows = organizations;

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (options?: { includeInactive?: boolean }) => {
		mockUseOrganizations(options);
		return {
			data: options?.includeInactive
				? organizationRows
				: organizationRows.filter((org) => org.is_active),
			isLoading: false,
			isFetching: false,
			refetch: vi.fn(),
		};
	},
	useCreateOrganization: () => {
		const [error, setError] = useState<Error | undefined>();
		const [isPending, setIsPending] = useState(false);

		const mutateAsync = async (variables: unknown) => {
			mockCreate(variables);
			setIsPending(true);
			try {
				if (nextCreateError) {
					const errorToThrow = nextCreateError;
					nextCreateError = undefined;
					setError(errorToThrow);
					throw errorToThrow;
				}
				const result =
					mockCreate.mock.results[mockCreate.mock.results.length - 1]
						?.value;
				await Promise.resolve(result);
				setError(undefined);
				return {};
			} finally {
				setIsPending(false);
			}
		};

		return {
			error,
			isPending,
			mutateAsync,
			reset: () => setError(undefined),
		};
	},
	useUpdateOrganization: () => {
		const [error, setError] = useState<Error | undefined>();
		const [isPending, setIsPending] = useState(false);

		const mutateAsync = async (variables: unknown) => {
			mockUpdate(variables);
			setIsPending(true);
			try {
				if (nextUpdateError) {
					const errorToThrow = nextUpdateError;
					nextUpdateError = undefined;
					setError(errorToThrow);
					throw errorToThrow;
				}
				const result =
					mockUpdate.mock.results[mockUpdate.mock.results.length - 1]
						?.value;
				await Promise.resolve(result);
				setError(undefined);
				return {};
			} finally {
				setIsPending(false);
			}
		};

		return {
			error,
			isPending,
			mutateAsync,
			reset: () => setError(undefined),
		};
	},
}));

const authz = vi.hoisted(() => ({
	canAt: (_permission: string, _target: { kind: string; id?: string }) =>
		true as boolean,
	canAnywhere: (_permission: string) => true as boolean,
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => authz,
}));

vi.mock("@/hooks/useSearch", () => ({
	useSearch: (items: unknown[]) => items,
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mockUseMediaQuery(),
}));

vi.mock("@/pages/settings/RequiredInstructionsSettings", () => ({
	RequiredInstructionsSettings: ({
		organizationId,
	}: {
		organizationId: string;
	}) => <div>Instructions for {organizationId}</div>,
}));

import { Organizations } from "./Organizations";

beforeEach(() => {
	authz.canAt = () => true;
	authz.canAnywhere = () => true;
	mockUseOrganizations.mockClear();
	mockCreate.mockReset();
	mockUpdate.mockReset();
	nextCreateError = undefined;
	nextUpdateError = undefined;
	organizationRows = organizations;
	mockUseMediaQuery.mockReturnValue(false);
});

describe("Organizations", () => {
	it("hides inactive organizations until requested", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		expect(screen.getByText("Acme")).toBeVisible();
		expect(screen.queryByText("Dormant Co")).not.toBeInTheDocument();

		await user.click(screen.getByRole("switch", { name: "Show Inactive" }));

		expect(screen.getByText("Dormant Co")).toBeVisible();
		expect(mockUseOrganizations).toHaveBeenLastCalledWith({
			includeInactive: true,
		});
	});

	it("opens a tabbed editor from the organization row", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		await user.click(screen.getByRole("row", { name: /Acme/ }));

		expect(
			screen.getByRole("heading", { name: "Edit Organization" }),
		).toBeVisible();
		expect(screen.getByRole("tab", { name: "General" })).toBeVisible();

		await user.click(screen.getByRole("tab", { name: "Instructions" }));

		expect(screen.getByText("Instructions for org-1")).toBeVisible();
	});

	it("paginates organizations with the shared table footer", async () => {
		const user = userEvent.setup();
		organizationRows = manyOrganizations;

		render(<Organizations />);

		expect(screen.getByText("Organization 1")).toBeVisible();
		expect(screen.queryByText("Organization 26")).not.toBeInTheDocument();
		expect(screen.getByText(/1.25 of 30/)).toBeInTheDocument();
		expect(
			screen
				.getByRole("navigation", { name: /pagination/i })
				.closest("tfoot"),
		).not.toBeNull();

		await user.click(screen.getByRole("button", { name: "Next" }));

		expect(screen.getByText("Organization 26")).toBeVisible();
		expect(screen.queryByText("Organization 1")).not.toBeInTheDocument();
		expect(screen.getByText(/26.30 of 30/)).toBeInTheDocument();
	});

	it("paginates organizations on mobile", async () => {
		const user = userEvent.setup();
		organizationRows = manyOrganizations;
		mockUseMediaQuery.mockReturnValue(true);

		render(<Organizations />);

		const list = screen.getByRole("list", { name: "Organizations" });
		expect(within(list).getByText("Organization 1")).toBeVisible();
		expect(
			within(list).queryByText("Organization 26"),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Next" }));

		expect(within(list).getByText("Organization 26")).toBeVisible();
		expect(
			within(list).queryByText("Organization 1"),
		).not.toBeInTheDocument();
	});

	it("saves organization status from the General tab", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		await user.click(screen.getByRole("row", { name: /Acme/ }));
		await user.click(
			screen.getByRole("switch", { name: "Organization Status" }),
		);
		await user.click(screen.getByRole("button", { name: "Save Changes" }));

		await waitFor(() =>
			expect(mockUpdate).toHaveBeenCalledWith({
				params: { path: { org_id: "org-1" } },
				body: {
					name: "Acme",
					domain: "acme.example",
					is_active: false,
				},
			}),
		);
	});

	it("offers a reversible disable action from the row menu", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		await user.click(screen.getByRole("button", { name: "Acme actions" }));
		await user.click(
			screen.getByRole("menuitem", { name: "Disable Acme" }),
		);
		expect(
			screen.getByRole("heading", { name: "Disable organization?" }),
		).toBeVisible();
		expect(screen.getByRole("alertdialog")).toHaveAccessibleDescription(
			"Acme will be removed from active organization lists. You can re-enable it later by showing inactive organizations.",
		);

		await user.click(screen.getByRole("button", { name: "Disable" }));

		await waitFor(() =>
			expect(mockUpdate).toHaveBeenCalledWith({
				params: { path: { org_id: "org-1" } },
				body: { is_active: false },
			}),
		);
	});

	it("retains a failed disable confirmation and retries the same organization", async () => {
		const user = userEvent.setup();
		nextUpdateError = new Error("Cannot update right now");
		render(<Organizations />);
		await user.click(screen.getByRole("button", { name: "Acme actions" }));
		await user.click(
			screen.getByRole("menuitem", { name: "Disable Acme" }),
		);
		await user.click(screen.getByRole("button", { name: "Disable" }));
		expect(
			within(screen.getByRole("alertdialog")).getByRole("alert"),
		).toHaveTextContent("Cannot update right now");
		await user.click(screen.getByRole("button", { name: "Disable" }));
		await waitFor(() =>
			expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
		);
		expect(mockUpdate).toHaveBeenCalledTimes(2);
		expect(mockUpdate).toHaveBeenLastCalledWith({
			params: { path: { org_id: "org-1" } },
			body: { is_active: false },
		});
	});

	it("returns focus to the edit opener after close on desktop and mobile", async () => {
		const user = userEvent.setup();

		const desktop = render(<Organizations />);
		await user.click(screen.getByRole("button", { name: "Acme actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit Acme" }));
		const nameField = screen.getByLabelText("Organization Name");
		await user.clear(nameField);
		await user.type(nameField, "Acme Renamed");
		await user.click(screen.getByRole("button", { name: "Save Changes" }));
		await waitFor(() => {
			const row = document.querySelector<HTMLElement>(
				'[data-org-id="org-1"]',
			);
			expect(
				row?.querySelector<HTMLButtonElement>(
					'button[aria-label$="actions"]',
				),
			).toHaveFocus();
		});
		desktop.unmount();

		mockUseMediaQuery.mockReturnValue(true);
		render(<Organizations />);
		await user.click(screen.getByRole("button", { name: "Acme actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit Acme" }));
		await user.click(screen.getByRole("button", { name: "Save Changes" }));
		expect(
			screen.getByRole("button", { name: "Acme actions" }),
		).toHaveFocus();
	});

	it("keeps create drafts open when create fails", async () => {
		const user = userEvent.setup();
		nextCreateError = new Error("Synthetic create failure");

		render(<Organizations />);

		await user.click(
			screen.getByRole("button", { name: "New Organization" }),
		);
		await user.type(
			screen.getByLabelText("Organization Name"),
			"Failing Org",
		);
		await user.click(
			screen.getByRole("button", { name: "Create Organization" }),
		);

		const dialog = screen.getByRole("dialog", {
			name: "Create Organization",
		});
		expect(dialog).toBeVisible();
		expect(within(dialog).getByRole("alert")).toHaveFocus();
		expect(
			within(dialog).getByText("Failed to create organization"),
		).toBeVisible();
		expect(
			within(dialog).getByText("Synthetic create failure"),
		).toBeVisible();
		expect(screen.getByLabelText("Organization Name")).toHaveValue(
			"Failing Org",
		);

		await user.click(
			within(dialog).getByRole("button", { name: "Retry create" }),
		);

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(2));
		await waitFor(() =>
			expect(
				screen.queryByRole("dialog", { name: "Create Organization" }),
			).not.toBeInTheDocument(),
		);
	});

	it("keeps edit drafts open when save fails", async () => {
		const user = userEvent.setup();
		nextUpdateError = new Error("Synthetic update failure");

		render(<Organizations />);

		await user.click(screen.getByRole("row", { name: /Acme/ }));
		const nameField = screen.getByLabelText("Organization Name");
		await user.clear(nameField);
		await user.type(nameField, "Acme Edited");
		await user.click(screen.getByRole("button", { name: "Save Changes" }));

		const dialog = screen.getByRole("dialog", {
			name: "Edit Organization",
		});
		expect(dialog).toBeVisible();
		expect(within(dialog).getByRole("alert")).toHaveFocus();
		expect(
			within(dialog).getByText("Failed to update organization"),
		).toBeVisible();
		expect(
			within(dialog).getByText("Synthetic update failure"),
		).toBeVisible();
		expect(screen.getByLabelText("Organization Name")).toHaveValue(
			"Acme Edited",
		);

		await user.click(
			within(dialog).getByRole("button", { name: "Retry save" }),
		);

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(2));
		await waitFor(() =>
			expect(
				screen.queryByRole("dialog", { name: "Edit Organization" }),
			).not.toBeInTheDocument(),
		);
	});

	it("locks the edit tab strip and form fields while save is pending", async () => {
		const user = userEvent.setup();
		let resolveUpdate!: () => void;
		mockUpdate.mockImplementationOnce(
			() =>
				new Promise<void>((resolve) => {
					resolveUpdate = resolve;
				}),
		);

		render(<Organizations />);

		await user.click(screen.getByRole("button", { name: "Acme actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit Acme" }));
		await user.click(screen.getByRole("button", { name: "Save Changes" }));

		const dialog = screen.getByRole("dialog", {
			name: "Edit Organization",
		});
		expect(dialog).toHaveAttribute("aria-busy", "true");
		expect(dialog).toHaveAttribute("inert");

		await user.click(screen.getByRole("tab", { name: "Instructions" }));
		expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute(
			"aria-selected",
			"true",
		);

		await act(async () => {
			resolveUpdate();
		});

		await waitFor(() =>
			expect(
				screen.queryByRole("dialog", { name: "Edit Organization" }),
			).not.toBeInTheDocument(),
		);
	});

	it("keeps a pending create dialog open until the mutation settles", async () => {
		const user = userEvent.setup();
		let resolveCreate!: () => void;
		mockCreate.mockImplementationOnce(
			() =>
				new Promise<void>((resolve) => {
					resolveCreate = resolve;
				}),
		);

		render(<Organizations />);

		await user.click(
			screen.getByRole("button", { name: "New Organization" }),
		);
		await user.type(
			screen.getByLabelText("Organization Name"),
			"Pending Org",
		);
		await user.click(
			screen.getByRole("button", { name: "Create Organization" }),
		);

		expect(
			screen.getByRole("button", { name: "Creating..." }),
		).toBeDisabled();
		await user.keyboard("{Escape}");
		expect(
			screen.getByRole("dialog", { name: "Create Organization" }),
		).toBeVisible();

		await act(async () => {
			resolveCreate();
		});

		await waitFor(() =>
			expect(
				screen.queryByRole("dialog", { name: "Create Organization" }),
			).not.toBeInTheDocument(),
		);
	});
});

describe("Organizations permissions", () => {
	it("shows a viewer the list without create, edit, or disable controls", () => {
		authz.canAt = () => false;
		authz.canAnywhere = () => false;
		render(<Organizations />);

		expect(screen.getByText("Acme")).toBeInTheDocument();
		expect(
			screen.getByText("Organizations you can view"),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /new organization/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Edit Acme" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Acme actions" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("columnheader", { name: "Actions" }),
		).not.toBeInTheDocument();
	});

	it("lets an organization's manager edit it but not create organizations", () => {
		authz.canAt = (_permission, target) =>
			target.kind === "org" && target.id === "org-1";
		render(<Organizations />);

		expect(
			screen.queryByRole("button", { name: /new organization/i }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Edit Acme" }),
		).toBeInTheDocument();
	});
});
