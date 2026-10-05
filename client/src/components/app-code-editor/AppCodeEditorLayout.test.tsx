import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { act } from "@testing-library/react";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { AppCodeEditorLayout } from "./AppCodeEditorLayout";

const state = vi.hoisted(() => ({
	desktop: false,
	resolveSave: null as null | ((response: Response) => void),
}));

const authFetch = vi.hoisted(() => vi.fn());
const bundledAppShell = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({
	success: vi.fn(),
	error: vi.fn(),
	info: vi.fn(),
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => state.desktop,
}));
vi.mock("@/hooks/useAppCodeUpdates", () => ({ useAppCodeUpdates: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ authFetch }));
vi.mock("sonner", () => ({ toast }));

vi.mock("@/components/file-tree", () => ({
	createAppCodeOperations: () => ({}),
	appCodeIconResolver: vi.fn(),
	validateAppCodePath: vi.fn(),
	FileTree: ({
		editor,
	}: {
		editor: { onFileOpen: (file: unknown, content: unknown) => void };
	}) => (
		<div>
			<button
				onClick={() =>
					editor.onFileOpen(
						{ path: "pages/index.tsx", name: "index.tsx" },
						{ content: "original" },
					)
				}
			>
				Open index.tsx
			</button>
			<button
				onClick={() =>
					editor.onFileOpen(
						{ path: "pages/notes.tsx", name: "notes.tsx" },
						{ content: "second file" },
					)
				}
			>
				Open notes.tsx
			</button>
		</div>
	),
}));
vi.mock("./AppCodeEditor", () => ({
	AppCodeEditor: ({
		value,
		onChange,
	}: {
		value: string;
		onChange: (value: string) => void;
	}) => (
		<textarea
			aria-label="Code buffer"
			value={value}
			onChange={(event) => onChange(event.target.value)}
		/>
	),
}));
vi.mock("./DependencyPanel", () => ({
	DependencyPanel: () => <div>Package manager</div>,
}));
vi.mock("@/components/jsx-app/BundledAppShell", () => ({
	BundledAppShell: (props: unknown) => {
		bundledAppShell(props);
		return <div>Running app preview</div>;
	},
}));

beforeEach(() => {
	state.desktop = false;
	state.resolveSave = null;
	bundledAppShell.mockClear();
	authFetch.mockReset();
	toast.success.mockReset();
	toast.error.mockReset();
	toast.info.mockReset();
	authFetch.mockResolvedValue(
		new Response(JSON.stringify({ compiled: "compiled" }), {
			status: 200,
			headers: { "Content-Type": "application/json" },
		}),
	);
});

describe("App editor responsive workspace", () => {
	it("forwards the owning Solution to the app preview", async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<AppCodeEditorLayout appId="fixture" solutionId="solution-1" />,
		);
		await user.click(screen.getByRole("button", { name: "App preview" }));
		expect(bundledAppShell).toHaveBeenCalledWith(
			expect.objectContaining({ solutionId: "solution-1", isPreview: true }),
		);
	});

	it("preserves one code buffer while switching mobile tools and desktop layout", async () => {
		state.desktop = false;
		const user = userEvent.setup();
		const props = { appId: "fixture", appName: "Review app" };
		const view = renderWithProviders(<AppCodeEditorLayout {...props} />);

		await user.click(
			screen.getByRole("button", { name: "Open index.tsx" }),
		);
		expect(
			screen.queryByRole("button", { name: "Open index.tsx" }),
		).not.toBeInTheDocument();

		await user.type(
			screen.getByRole("textbox", { name: "Code buffer" }),
			" edited",
		);

		await user.click(
			screen.getByRole("button", { name: "Show files and packages" }),
		);
		expect(
			screen.queryByRole("textbox", { name: "Code buffer" }),
		).not.toBeInTheDocument();
		expect(
			screen.getAllByRole("textbox", {
				name: "Code buffer",
				hidden: true,
			}),
		).toHaveLength(1);

		await user.click(screen.getByRole("button", { name: "Code" }));
		expect(
			screen.getByRole("textbox", { name: "Code buffer" }),
		).toHaveValue("original edited");

		await user.click(screen.getByRole("button", { name: "App preview" }));
		expect(screen.getByText("Running app preview")).toBeVisible();
		expect(
			screen.queryByRole("textbox", { name: "Code buffer" }),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Code" }));
		expect(
			screen.getByRole("textbox", { name: "Code buffer" }),
		).toHaveValue("original edited");

		state.desktop = true;
		view.rerender(<AppCodeEditorLayout {...props} />);
		expect(
			screen.getByRole("button", { name: "Open index.tsx" }),
		).toBeVisible();
		expect(
			screen.getByRole("textbox", { name: "Code buffer" }),
		).toHaveValue("original edited");
	});
});

describe("App editor file switching", () => {
	it("loads a file cleanly without marking it unsaved", async () => {
		state.desktop = true;
		const user = userEvent.setup();
		renderWithProviders(
			<AppCodeEditorLayout appId="fixture" appName="Review app" />,
		);

		await user.click(
			screen.getByRole("button", { name: "Open index.tsx" }),
		);
		expect(
			screen.getByRole("textbox", { name: "Code buffer" }),
		).toHaveValue("original");
		expect(screen.queryByText("(unsaved)")).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
	});

	it("prompts before discarding a dirty buffer and preserves the buffer on cancel", async () => {
		state.desktop = true;
		const user = userEvent.setup();
		renderWithProviders(
			<AppCodeEditorLayout appId="fixture" appName="Review app" />,
		);

		await user.click(
			screen.getByRole("button", { name: "Open index.tsx" }),
		);
		await user.type(
			screen.getByRole("textbox", { name: "Code buffer" }),
			" edited",
		);
		await user.click(
			screen.getByRole("button", { name: "Open notes.tsx" }),
		);

		const dialog = await screen.findByRole("alertdialog");
		expect(dialog).toHaveAccessibleDescription(
			"Open notes.tsx? Your unsaved changes to index.tsx will be lost.",
		);

		await user.click(screen.getByRole("button", { name: "Keep editing" }));
		expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
		expect(
			screen.getByRole("textbox", { name: "Code buffer" }),
		).toHaveValue("original edited");

		await user.click(
			screen.getByRole("button", { name: "Open notes.tsx" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Discard changes" }),
		);
		expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
		expect(
			screen.getByRole("textbox", { name: "Code buffer" }),
		).toHaveValue("second file");
	});

	it("blocks file switching while a save is pending", async () => {
		state.desktop = true;
		const user = userEvent.setup();
		let releaseSave!: (response: Response) => void;
		authFetch.mockImplementation(
			() =>
				new Promise<Response>((resolve) => {
					releaseSave = resolve;
				}),
		);

		renderWithProviders(
			<AppCodeEditorLayout appId="fixture" appName="Review app" />,
		);

		await user.click(
			screen.getByRole("button", { name: "Open index.tsx" }),
		);
		await user.type(
			screen.getByRole("textbox", { name: "Code buffer" }),
			" edited",
		);
		await user.click(screen.getByRole("button", { name: "Save" }));
		expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();

		await user.click(
			screen.getByRole("button", { name: "Open notes.tsx" }),
		);
		expect(toast.error).toHaveBeenCalledWith("Save in progress", {
			description:
				"Wait for the current save to finish before switching files.",
		});
		expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
		expect(
			screen.getByRole("textbox", { name: "Code buffer" }),
		).toHaveValue("original edited");

		await act(async () => {
			releaseSave(
				new Response(JSON.stringify({ compiled: "compiled" }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				}),
			);
		});

		await waitFor(() =>
			expect(
				screen.queryByRole("button", { name: "Saving…" }),
			).not.toBeInTheDocument(),
		);
	});
});
