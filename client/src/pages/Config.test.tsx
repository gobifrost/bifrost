import { Config } from "./Config";
/**
 * Tests for the Config list page.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";

const mockUseMediaQuery = vi.fn(() => false);
const mockUseAuth = vi.fn(() => ({ isPlatformAdmin: false }));
const mockExport = vi.fn();
const mockImportDialog = vi.fn();

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mockUseMediaQuery(),
}));

vi.mock("@/services/exportImport", () => ({
	exportEntities: (...args: unknown[]) => mockExport(...args),
}));

const mockUseConfigs = vi.fn();
const mockUseDeleteConfig = vi.fn();

vi.mock("@/hooks/useConfig", () => ({
	useConfigs: (...a: unknown[]) => mockUseConfigs(...a),
	useDeleteConfig: (...a: unknown[]) => mockUseDeleteConfig(...a),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => ({
		scope: { orgName: "Acme" },
		isGlobalScope: false,
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/components/config/ConfigDialog", () => ({
	ConfigDialog: ({
		config,
		open,
	}: {
		config?: { key?: string };
		open: boolean;
	}) =>
		open ? (
			<div role="dialog">Config dialog {config?.key ?? "new"}</div>
		) : null,
}));

vi.mock("@/components/ImportDialog", () => ({
	ImportDialog: (props: {
		open: boolean;
		entityType: string;
		onImportComplete?: () => void;
	}) => {
		mockImportDialog(props);
		return props.open ? (
			<div role="dialog" aria-label={`Import ${props.entityType}`}>
				Import {props.entityType}
			</div>
		) : null;
	},
}));

const regularConfig = {
	id: "cfg-1",
	key: "api_token",
	value: "x",
	type: "string",
	scope: "global",
	org_id: null,
	description: "",
	integration_name: null,
};

beforeEach(() => {
	vi.clearAllMocks();
	mockUseMediaQuery.mockReturnValue(false);
	mockUseAuth.mockReturnValue({ isPlatformAdmin: false });
	mockUseConfigs.mockReturnValue({
		data: [regularConfig],
		isFetching: false,
		refetch: vi.fn(),
	});
	mockUseDeleteConfig.mockReturnValue({ mutateAsync: vi.fn() });
});

async function renderPage() {
	return renderWithProviders(<Config />);
}

describe("Config — list", () => {
	it("opens from a noninteractive desktop row cell", async () => {
		await renderPage();
		fireEvent.click(screen.getByRole("cell", { name: "x" }));
		expect(screen.getByRole("dialog")).toBeInTheDocument();
	});

	it("fetches without include_orphaned (orphaned UI stripped)", async () => {
		await renderPage();
		expect(mockUseConfigs).toHaveBeenLastCalledWith(undefined);
		expect(
			screen.queryByRole("checkbox", { name: /show orphaned/i }),
		).toBeNull();
	});

	it("renders zero, false and JSON without exposing secret values on mobile", async () => {
		mockUseMediaQuery.mockReturnValue(true);
		mockUseConfigs.mockReturnValue({
			data: [
				{
					...regularConfig,
					id: "zero",
					key: "interval",
					value: 0,
					type: "int",
				},
				{
					...regularConfig,
					id: "false",
					key: "enabled",
					value: false,
					type: "bool",
				},
				{
					...regularConfig,
					id: "json",
					key: "settings",
					value: { region: "east" },
					type: "json",
				},
				{
					...regularConfig,
					id: "secret",
					key: "credential",
					value: "synthetic-hidden-value",
					type: "secret",
				},
			],
			isFetching: false,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		expect(screen.getByText("0", { exact: true })).toBeVisible();
		expect(screen.getByText("false", { exact: true })).toBeVisible();
		expect(
			screen.getByText('{"region":"east"}', { exact: true }),
		).toBeVisible();
		expect(
			screen.queryByText("synthetic-hidden-value"),
		).not.toBeInTheDocument();
	});

	it("leads the mobile config summary with the organization", async () => {
		mockUseMediaQuery.mockReturnValue(true);
		mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
		mockUseConfigs.mockReturnValue({
			data: [{ ...regularConfig, scope: "org", org_id: "org-1" }],
			isFetching: false,
			refetch: vi.fn(),
		});
		await renderPage();
		const terms = screen.getAllByRole("term");
		expect(terms[0]).toHaveTextContent("Organization");
		expect(terms[1]).toHaveTextContent("Value");
	});

	it("exports independent UUID selections for matching keys in different organizations", async () => {
		mockUseMediaQuery.mockReturnValue(true);
		mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
		mockUseConfigs.mockReturnValue({
			data: [
				{
					...regularConfig,
					id: "11111111-1111-4111-8111-111111111111",
					scope: "org",
					org_id: "org-1",
				},
				{
					...regularConfig,
					id: "22222222-2222-4222-8222-222222222222",
					scope: "org",
					org_id: "org-2",
				},
			],
			isFetching: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		const selections = screen.getAllByRole("checkbox", {
			name: "Select api_token",
		});
		await user.click(selections[0]);
		expect(selections[1]).not.toBeChecked();
		await user.click(screen.getByRole("button", { name: "Export (1)" }));
		expect(mockExport).toHaveBeenCalledWith("configs", [
			"11111111-1111-4111-8111-111111111111",
		]);
	});

	it("exports all configs when nothing is selected", async () => {
		mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
		mockExport.mockResolvedValueOnce(undefined);
		const { user } = await renderPage();

		await user.click(screen.getByRole("button", { name: "Export All" }));

		expect(mockExport).toHaveBeenCalledWith("configs", []);
	});

	it("opens the config import dialog and refetches when import completes", async () => {
		const refetch = vi.fn();
		mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
		mockUseConfigs.mockReturnValue({
			data: [regularConfig],
			isFetching: false,
			refetch,
		});
		const { user } = await renderPage();

		await user.click(screen.getByRole("button", { name: "Import" }));

		expect(
			screen.getByRole("dialog", { name: "Import configs" }),
		).toBeVisible();
		const latestDialogProps = mockImportDialog.mock.calls.at(-1)?.[0] as {
			onImportComplete: () => void;
		};
		latestDialogProps.onImportComplete();
		expect(refetch).toHaveBeenCalledOnce();
	});

	it("shows retry without removing cached data", async () => {
		const refetch = vi.fn();
		mockUseConfigs.mockReturnValue({
			data: [regularConfig],
			isError: true,
			isFetching: false,
			refetch,
		});
		const { user } = await renderPage();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Configuration could not be refreshed",
		);
		expect(screen.getByText("api_token", { exact: true })).toBeVisible();
		await user.click(
			screen.getByRole("button", { name: "Retry configuration" }),
		);
		expect(refetch).toHaveBeenCalledOnce();
	});

	it("retains the delete confirmation on failure and retries the same record", async () => {
		const deletion = vi
			.fn()
			.mockRejectedValueOnce({ detail: "Synthetic delete failure" })
			.mockResolvedValueOnce(undefined);
		mockUseDeleteConfig.mockReturnValue({ mutateAsync: deletion });
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("button", { name: /more actions for api_token/i }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		await user.click(
			screen.getByRole("button", { name: /delete configuration/i }),
		);
		await waitFor(() => expect(screen.getByRole("alert")).toHaveFocus());
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Synthetic delete failure",
		);
		await user.click(
			screen.getByRole("button", { name: /delete configuration/i }),
		);
		await waitFor(() =>
			expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
		);
		expect(deletion).toHaveBeenNthCalledWith(2, {
			params: { path: { config_id: "cfg-1" } },
		});
	});

	it("opens the config editor from the key and keeps delete in the overflow menu", async () => {
		mockUseMediaQuery.mockReturnValue(true);
		const deleteMutation = vi.fn();
		mockUseDeleteConfig.mockReturnValue({ mutateAsync: deleteMutation });
		const { user } = await renderPage();

		await user.click(screen.getByRole("button", { name: "api_token" }));
		expect(await screen.findByRole("dialog")).toHaveTextContent(
			"Config dialog api_token",
		);

		await user.click(
			screen.getByRole("button", {
				name: /more actions for api_token/i,
			}),
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		expect(
			screen.getByRole("alertdialog", { name: /delete configuration/i }),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: /delete config/i }),
		);
		expect(deleteMutation).toHaveBeenCalledOnce();
	});
});
