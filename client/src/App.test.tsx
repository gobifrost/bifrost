import { useEffect } from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
	Link,
	Outlet,
	RouterProvider,
	createMemoryRouter,
	useLocation,
} from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AppFrame, QualityRedirect } from "./App";

vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => ({ brandingLoaded: true }),
	OrgScopeProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Bifrost",
}));

vi.mock("@/contexts/KeyboardContext", () => ({
	useCmdCtrlShortcut: vi.fn(),
	KeyboardProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/stores/quickAccessStore", () => ({
	useQuickAccessStore: (selector: (state: unknown) => unknown) =>
		selector({
			isOpen: false,
			openQuickAccess: vi.fn(),
			closeQuickAccess: vi.fn(),
		}),
}));

vi.mock("@/stores/editorStore", () => ({
	useEditorStore: (selector: (state: unknown) => unknown) =>
		selector({
			isOpen: false,
			openEditor: vi.fn(),
		}),
}));

vi.mock("@/components/quick-access/QuickAccess", () => ({
	QuickAccess: () => null,
}));

vi.mock("@/components/editor/EditorOverlay", () => ({
	EditorOverlay: () => null,
}));

vi.mock("@/components/layout/UnifiedDock", () => ({
	UnifiedDock: () => null,
}));

vi.mock("@/components/ApplicationUpdateScreen", () => ({
	ApplicationUpdateGate: ({ children }: { children: React.ReactNode }) =>
		children,
}));

function PersistentLayout({ onMount }: { onMount: () => void }) {
	useEffect(() => {
		onMount();
	}, [onMount]);

	return (
		<div>
			<nav aria-label="Shell navigation">
				<Link to="/agents">Agents</Link>
				<Link to="/users">Users</Link>
			</nav>
			<Outlet />
		</div>
	);
}

describe("AppFrame shell lifetime", () => {
	it("keeps the shell layout mounted while sibling platform routes change", async () => {
		const onLayoutMount = vi.fn();
		const router = createMemoryRouter(
			[
				{
					element: <AppFrame />,
					children: [
						{
							path: "/",
							element: (
								<PersistentLayout onMount={onLayoutMount} />
							),
							children: [
								{ path: "agents", element: <h1>Agents</h1> },
								{ path: "users", element: <h1>Users</h1> },
							],
						},
					],
				},
			],
			{ initialEntries: ["/agents"] },
		);

		render(<RouterProvider router={router} />);
		expect(
			await screen.findByRole("heading", { name: "Agents" }),
		).toBeVisible();
		expect(onLayoutMount).toHaveBeenCalledTimes(1);

		await userEvent
			.setup()
			.click(screen.getByRole("link", { name: "Users" }));

		expect(
			await screen.findByRole("heading", { name: "Users" }),
		).toBeVisible();
		expect(onLayoutMount).toHaveBeenCalledTimes(1);
	});
});

describe("QualityRedirect", () => {
	it("preserves query parameters from the retired tune route", async () => {
		const router = createMemoryRouter(
			[
				{ path: "/agents/:id/tune", element: <QualityRedirect /> },
				{
					path: "/agents/:id/quality",
					element: <LocationProbe />,
				},
			],
			{
				initialEntries: [
					"/agents/agent-1/tune?tab=changes&suite=suite-1",
				],
			},
		);

		render(<RouterProvider router={router} />);

		expect(await screen.findByTestId("location-probe")).toHaveTextContent(
			"/agents/agent-1/quality?tab=changes&suite=suite-1",
		);
	});

	it("forces retired studio links to the tests tab while preserving other params", async () => {
		const router = createMemoryRouter(
			[
				{
					path: "/agents/:id/studio",
					element: <QualityRedirect defaultTab="tests" />,
				},
				{
					path: "/agents/:id/quality",
					element: <LocationProbe />,
				},
			],
			{
				initialEntries: [
					"/agents/agent-1/studio?tab=changes&suite=suite-1",
				],
			},
		);

		render(<RouterProvider router={router} />);

		expect(await screen.findByTestId("location-probe")).toHaveTextContent(
			"/agents/agent-1/quality?tab=tests&suite=suite-1",
		);
	});
});

describe("Agent Workbench routes", () => {
	it("keeps the literal fleet compatibility route before the agent route", () => {
		const appSource = readFileSync(join(process.cwd(), "src/App.tsx"), "utf8");

		expect(appSource.indexOf('path="agents/quality"')).toBeGreaterThan(-1);
		expect(appSource.indexOf('path="agents/quality"')).toBeLessThan(
			appSource.indexOf('path="agents/:id"'),
		);
	});
});

function LocationProbe() {
	const location = useLocation();
	return (
		<div data-testid="location-probe">
			{location.pathname}
			{location.search}
		</div>
	);
}
