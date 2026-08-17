/**
 * Component tests for AppReplacePathDialog.
 *
 * Focus: the dialog's decision logic — when does it warn, when does it
 * block Replace, when does force unblock, phase transitions on submit.
 * Hooks are mocked at the module level so these tests stay fast and do
 * not require a running backend.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import type { ApplicationPublic } from "@/hooks/useApplications";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockReplace = vi.fn();
const mockValidate = vi.fn();
const mockList = vi.fn();

vi.mock("@/hooks/useApplications", () => ({
	useApplications: () => ({ data: mockAppList() }),
	useReplaceApplication: () => ({ mutateAsync: mockReplace }),
	useValidateApplication: () => ({ mutateAsync: mockValidate }),
}));

vi.mock("@/components/file-tree/adapters/workspaceOperations", () => ({
	workspaceOperations: {
		list: (path: string) => mockList(path),
	},
}));

// appListRef lets individual tests override the apps list before render.
let appListRef: { applications: ApplicationPublic[] } = { applications: [] };
function mockAppList() {
	return appListRef;
}

// -----------------------------------------------------------------------------
// Fixtures
// -----------------------------------------------------------------------------

function makeApp(overrides: Partial<ApplicationPublic> = {}): ApplicationPublic {
	return {
		id: "app-1",
		name: "My App",
		slug: "my-app",
		description: null,
		icon: null,
		organization_id: null,
		published_at: null,
		created_at: "2026-04-20T00:00:00Z",
		updated_at: "2026-04-20T00:00:00Z",
		created_by: null,
		is_published: false,
		has_unpublished_changes: false,
		access_level: "authenticated",
		app_model: "inline_v1",
		runtime_mode: "trusted",
		is_solution_managed: false,
		role_ids: [],
		repo_path: "apps/my-app",
		...overrides,
	};
}

async function renderDialog(app: ApplicationPublic = makeApp()) {
	// Dynamic import so the vi.mock() calls above land before the component
	// resolves its dependencies.
	const { AppReplacePathDialog } = await import("./AppReplacePathDialog");
	const onClose = vi.fn();
	const onSuccess = vi.fn();
	const utils = renderWithProviders(
		<AppReplacePathDialog
			app={app}
			open={true}
			onClose={onClose}
			onSuccess={onSuccess}
		/>,
	);
	return { ...utils, onClose, onSuccess };
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

beforeEach(() => {
	mockReplace.mockReset();
	mockValidate.mockReset();
	mockList.mockReset();
	// Default: target folder has files (source-exists passes) and root list is empty.
	mockList.mockResolvedValue([
		{ path: "apps/bar/index.tsx", name: "index.tsx", type: "file" },
	]);
	appListRef = { applications: [] };
});

describe("AppReplacePathDialog — warnings", () => {
	it("warns when the chosen path already belongs to another app", async () => {
		appListRef = {
			applications: [
				makeApp(),
				makeApp({ id: "app-2", name: "Other App", repo_path: "apps/other" }),
			],
		};
		const { user, getByLabelText } = await renderDialog();

		await user.type(getByLabelText(/new path/i), "apps/other");

		expect(
			await screen.findByText(/already claimed by "Other App"/i),
		).toBeInTheDocument();

		const replaceBtn = screen.getByRole("button", { name: /^replace$/i });
		expect(replaceBtn).toBeDisabled();
	});

	it("warns when the chosen path is nested under another app", async () => {
		appListRef = {
			applications: [
				makeApp(),
				makeApp({ id: "app-2", name: "Other", repo_path: "apps/other" }),
			],
		};
		const { user, getByLabelText } = await renderDialog();

		await user.type(getByLabelText(/new path/i), "apps/other/nested");

		expect(
			await screen.findByText(/nested under "Other"/i),
		).toBeInTheDocument();
	});

	it("warns when the chosen path would contain another app", async () => {
		appListRef = {
			applications: [
				makeApp(),
				makeApp({
					id: "app-2",
					name: "Inner",
					repo_path: "apps/outer/inner",
				}),
			],
		};
		const { user, getByLabelText } = await renderDialog();

		await user.type(getByLabelText(/new path/i), "apps/outer");

		expect(
			await screen.findByText(/would contain "Inner"/i),
		).toBeInTheDocument();
	});

	it("warns when the chosen folder has no source files", async () => {
		// Override: target folder has no files.
		mockList.mockResolvedValue([
			{ path: "apps/empty/sub", name: "sub", type: "folder" },
		]);
		const { user, getByLabelText } = await renderDialog();

		await user.type(getByLabelText(/new path/i), "apps/empty");

		expect(
			await screen.findByText(/no source files found/i),
		).toBeInTheDocument();
	});

	it("does not warn when the target matches the app's current path", async () => {
		const app = makeApp({ repo_path: "apps/my-app" });
		const { user, getByLabelText } = await renderDialog(app);

		await user.type(getByLabelText(/new path/i), "apps/my-app");

		// No warnings of any kind are rendered.
		expect(screen.queryByText(/already claimed/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/no source files/i)).not.toBeInTheDocument();
	});
});

describe("AppReplacePathDialog — force toggle", () => {
	it("force unblocks Replace when a uniqueness warning is active", async () => {
		appListRef = {
			applications: [
				makeApp(),
				makeApp({ id: "app-2", name: "Other", repo_path: "apps/other" }),
			],
		};
		const { user, getByLabelText } = await renderDialog();

		await user.type(getByLabelText(/new path/i), "apps/other");

		const replaceBtn = screen.getByRole("button", { name: /^replace$/i });
		expect(replaceBtn).toBeDisabled();

		await user.click(screen.getByRole("button", { name: /advanced/i }));
		await user.click(screen.getByRole("checkbox", { name: /force/i }));

		expect(replaceBtn).toBeEnabled();
	});
});

describe("AppReplacePathDialog — phase transitions", () => {
	it("submits replace then shows validation results", async () => {
		mockReplace.mockResolvedValue({ ...makeApp(), repo_path: "apps/bar" });
		mockValidate.mockResolvedValue({
			valid: true,
			errors: [],
			warnings: [],
		});
		const { user, getByLabelText, onSuccess } = await renderDialog();

		await user.type(getByLabelText(/new path/i), "apps/bar");
		await user.click(screen.getByRole("button", { name: /^replace$/i }));

		expect(
			await screen.findByText(/no issues found/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /open app/i }),
		).toBeInTheDocument();

		expect(mockReplace).toHaveBeenCalledWith({
			params: { path: { app_id: "app-1" } },
			body: { repo_path: "apps/bar", force: false },
		});
		expect(onSuccess).toHaveBeenCalledTimes(1);
	});

	it("shows validation errors grouped by severity", async () => {
		mockReplace.mockResolvedValue({ ...makeApp(), repo_path: "apps/bar" });
		mockValidate.mockResolvedValue({
			valid: false,
			errors: [
				{
					severity: "error",
					kind: "unknown_component",
					file: "pages/home.tsx",
					message: "Unknown component <Foo/>",
				},
			],
			warnings: [
				{
					severity: "warning",
					kind: "bad_import",
					file: "pages/home.tsx",
					message: "Import not resolved",
				},
			],
		});
		const { user, getByLabelText } = await renderDialog();

		await user.type(getByLabelText(/new path/i), "apps/bar");
		await user.click(screen.getByRole("button", { name: /^replace$/i }));

		const errorsPanel = await screen.findByText(/errors \(1\)/i);
		expect(errorsPanel).toBeInTheDocument();
		expect(
			within(errorsPanel.parentElement!.parentElement!).getByText(
				/Unknown component/,
			),
		).toBeInTheDocument();

		expect(screen.getByText(/warnings \(1\)/i)).toBeInTheDocument();
	});

	it("returns to pick phase if the replace call fails", async () => {
		mockReplace.mockRejectedValue(new Error("boom"));
		const { user, getByLabelText } = await renderDialog();

		await user.type(getByLabelText(/new path/i), "apps/bar");
		await user.click(screen.getByRole("button", { name: /^replace$/i }));

		// Still on the pick phase (input remains visible, no validation panel).
		expect(await screen.findByLabelText(/new path/i)).toBeInTheDocument();
		expect(screen.queryByText(/no issues found/i)).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /open app/i }),
		).not.toBeInTheDocument();
	});
});
