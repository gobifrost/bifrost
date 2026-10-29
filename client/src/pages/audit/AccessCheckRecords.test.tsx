import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { within } from "@testing-library/react";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import type { AuditLogEntry } from "@/hooks/useAuditLog";

const list = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/useAuditLog", () => ({
	useAuditLog: (...args: unknown[]) => list(...args),
}));

import { AccessCheckRecords } from "./AccessCheckRecords";

const first: AuditLogEntry = {
	id: "check-1",
	timestamp: "2026-10-17T12:30:00Z",
	action: "access.check",
	resource_type: "permission",
	resource_id: null,
	outcome: "failure",
	source: "http",
	operation_id: "workflows.execute",
	surface: "workflow",
	execution_id: null,
	actor: {
		user_id: "user-1",
		user_email: "operator@contoso.example",
		user_name: "Contoso Operator",
		organization_id: "org-1",
		organization_name: "Fabrikam",
		home_organization_id: "org-2",
		home_organization_name: "Contoso",
	},
	workflow_name: "Nightly Sync",
	ip_address: null,
	user_agent: null,
	details: null,
};
const second = { ...first, id: "check-2", timestamp: "2026-10-17T12:20:00Z" };
const nextPage = {
	...first,
	id: "check-11",
	timestamp: "2026-10-17T12:10:00Z",
};
const filters = { workflow_id: "workflow-1", aggregate_key: "window-1" };
const query = (
	entries: AuditLogEntry[],
	continuation_token: string | null = null,
) => ({
	data: { entries, continuation_token },
	isLoading: false,
	isFetching: false,
	error: null,
	refetch: vi.fn(),
});

beforeEach(() => {
	list.mockReset();
	list.mockImplementation((params: { continuation_token?: string }) =>
		params.continuation_token
			? query([nextPage])
			: query([first, second], "next-page"),
	);
});

describe("AccessCheckRecords", () => {
	it("distinguishes checks recorded within the same second", () => {
		list.mockReturnValue(
			query([
				{ ...first, timestamp: "2026-10-17T12:30:00.100Z" },
				{ ...second, timestamp: "2026-10-17T12:30:00.200Z" },
			]),
		);
		renderWithProviders(
			<AccessCheckRecords
				filters={filters}
				count={2}
				selectedId={first.id}
				onSelect={vi.fn()}
			/>,
		);
		const times = within(screen.getByRole("list", { name: "Check Times" }));
		expect(times.getByRole("button", { name: /00\.100/ })).toBeVisible();
		expect(times.getByRole("button", { name: /00\.200/ })).toBeVisible();
	});

	it("selects an individual check by time without repeating its shared columns", async () => {
		const onSelect = vi.fn();
		const { user } = renderWithProviders(
			<AccessCheckRecords
				filters={filters}
				count={12}
				selectedId={first.id}
				onSelect={onSelect}
			/>,
		);
		const times = within(screen.getByRole("list", { name: "Check Times" }));
		const buttons = times.getAllByRole("button");
		expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
		await user.click(buttons[1]);
		expect(onSelect).toHaveBeenCalledExactlyOnceWith(second);
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		expect(
			screen.queryByText("operator@contoso.example"),
		).not.toBeInTheDocument();
		expect(screen.queryByText("Fabrikam")).not.toBeInTheDocument();
	});

	it("pages within the selected summary and selects a check on the new page", async () => {
		function Harness() {
			const [selected, setSelected] = useState(first);
			return (
				<>
					<output aria-label="Selected Check">{selected.id}</output>
					<AccessCheckRecords
						filters={filters}
						count={12}
						selectedId={selected.id}
						onSelect={setSelected}
					/>
				</>
			);
		}
		const { user } = renderWithProviders(<Harness />);
		expect(
			screen.getByRole("button", { name: "Previous Check Page" }),
		).toBeDisabled();
		await user.click(
			screen.getByRole("button", { name: "Next Check Page" }),
		);
		await waitFor(() =>
			expect(screen.getByLabelText("Selected Check")).toHaveTextContent(
				nextPage.id,
			),
		);
		expect(
			screen.getByRole("button", { name: "Next Check Page" }),
		).toBeDisabled();
		expect(list).toHaveBeenLastCalledWith(
			expect.objectContaining({
				...filters,
				action: "access.check",
				outcome: "failure",
				limit: 10,
				continuation_token: "next-page",
			}),
			true,
			{ preservePageData: true },
		);
		await user.click(
			screen.getByRole("button", { name: "Previous Check Page" }),
		);
		await waitFor(() =>
			expect(screen.getByLabelText("Selected Check")).toHaveTextContent(
				first.id,
			),
		);
	});

	it("shows a loading state and offers recovery from a read failure", async () => {
		list.mockReturnValue({
			data: undefined,
			isLoading: true,
			isFetching: true,
		});
		const props = {
			filters,
			count: 12,
			selectedId: first.id,
			onSelect: vi.fn(),
		};
		const { user, rerender } = renderWithProviders(
			<AccessCheckRecords {...props} />,
		);
		expect(screen.getByRole("status")).toHaveTextContent(
			"Loading recorded checks…",
		);
		expect(
			screen.getByRole("button", { name: "Next Check Page" }),
		).toBeDisabled();
		const refetch = vi.fn();
		list.mockReturnValue({
			data: undefined,
			isLoading: false,
			isFetching: false,
			error: new Error("Checks unavailable"),
			refetch,
		});
		rerender(<AccessCheckRecords {...props} />);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Checks unavailable",
		);
		await user.click(screen.getByRole("button", { name: "Try Again" }));
		expect(refetch).toHaveBeenCalledOnce();
	});
});
