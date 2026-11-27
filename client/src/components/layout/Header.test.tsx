import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const authorizationState = vi.hoisted(() => ({
	settingsRead: false,
	settingsReadWrite: false,
	repositoryReadWrite: false,
}));
const profileState = vi.hoisted(() => ({
	has_avatar: false,
	dataUpdatedAt: 0,
}));
const mockCanAt = vi.hoisted(() => vi.fn());
const runState = vi.hoisted(() => ({ enabled: true }));
const mockToggleTheme = vi.fn();
const mockNavigate = vi.fn();
const mockLogout = vi.fn();
const mockOpenEditor = vi.fn();
const mockOpenQuickAccess = vi.fn();
const mockUseMediaQuery = vi.fn((query: string) => query.includes("1279px"));

vi.mock("react-router-dom", async (original) => ({
	...(await original<typeof import("react-router-dom")>()),
	useNavigate: () => mockNavigate,
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		user: { email: "admin@example.com", name: "Admin User" },
		logout: mockLogout,
	}),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAt: mockCanAt,
	}),
}));

vi.mock("@/stores/editorStore", () => ({
	useEditorStore: (
		selector: (state: { openEditor: typeof mockOpenEditor }) => unknown,
	) => selector({ openEditor: mockOpenEditor }),
}));

vi.mock("@/stores/quickAccessStore", () => ({
	useQuickAccessStore: (
		selector: (state: {
			openQuickAccess: typeof mockOpenQuickAccess;
		}) => unknown,
	) => selector({ openQuickAccess: mockOpenQuickAccess }),
}));

vi.mock("@/hooks/useProfile", () => ({
	useProfile: () => ({
		data: {
			email: "admin@example.com",
			name: "Admin User",
			has_avatar: profileState.has_avatar,
		},
		dataUpdatedAt: profileState.dataUpdatedAt,
	}),
}));

vi.mock("@/services/profile", () => ({
	profileService: { getAvatarUrl: () => "/api/profile/avatar" },
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: (query: string) => mockUseMediaQuery(query),
}));

vi.mock("@/components/PasskeySetupBadge", () => ({
	PasskeySetupBadge: () => <button type="button">Set up passkey</button>,
}));

vi.mock("@/components/layout/NotificationCenter", () => ({
	NotificationCenter: () => <button type="button">Notifications</button>,
}));

vi.mock("@/contexts/ThemeContext", () => ({
	useTheme: () => ({ theme: "dark", toggleTheme: mockToggleTheme }),
}));

vi.mock("@/components/layout/BifrostRunMenu", () => ({
	useBifrostRunInfo: () => ({ data: runState }),
	BifrostRunMenu: ({ open }: { open: boolean }) =>
		open ? <div role="dialog" aria-label="Assistant Setup" /> : null,
}));

vi.mock("@/components/layout/HeaderStatusIndicators", () => ({
	HeaderStatusIndicators: () => <span>Workspace status indicator</span>,
}));

import { Header } from "./Header";

beforeEach(() => {
	runState.enabled = true;
	vi.clearAllMocks();
	vi.unstubAllGlobals();
	profileState.has_avatar = false;
	profileState.dataUpdatedAt = 0;
	authorizationState.settingsRead = true;
	authorizationState.settingsReadWrite = false;
	authorizationState.repositoryReadWrite = false;
	mockCanAt.mockImplementation(
		(permission: string) =>
			(permission === "settings.read" &&
				authorizationState.settingsRead) ||
			(permission === "settings.readwrite" &&
				authorizationState.settingsReadWrite) ||
			(permission === "repository.readwrite" &&
				authorizationState.repositoryReadWrite),
	);
	mockUseMediaQuery.mockImplementation((query: string) =>
		query.includes("1279px"),
	);
});

describe("Header", () => {
	it("keeps the mobile header to a single toolbar by omitting secondary content", () => {
		mockUseMediaQuery.mockImplementation(
			(query: string) =>
				query.includes("1279px") || query.includes("639px"),
		);

		renderWithProviders(<Header />);

		expect(
			screen.getByRole("button", { name: /open navigation/i }),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: /account menu/i }),
		).toBeVisible();
		expect(
			screen.queryByLabelText("Workspace status"),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /set up passkey/i }),
		).not.toBeInTheDocument();
	});

	it("keeps Help behind the existing Global settings permission", async () => {
		const { user, rerender } = renderWithProviders(<Header />);
		await user.click(screen.getByRole("button", { name: "Account menu" }));
		expect(screen.getByRole("menuitem", { name: "Help" })).toBeVisible();
		expect(mockCanAt).toHaveBeenCalledWith("settings.read", {
			kind: "global",
		});
		authorizationState.settingsRead = false;
		rerender(<Header />);
		expect(
			screen.queryByRole("menuitem", { name: "Help" }),
		).not.toBeInTheDocument();
	});

	it("launches the editor from the account menu only for a Global repository writer", async () => {
		const { user, rerender } = renderWithProviders(<Header />);
		await user.click(screen.getByRole("button", { name: "Account menu" }));
		expect(
			screen.queryByRole("menuitem", { name: /Open Editor/ }),
		).not.toBeInTheDocument();
		authorizationState.repositoryReadWrite = true;
		rerender(<Header />);
		await user.click(screen.getByRole("menuitem", { name: /Open Editor/ }));
		expect(mockOpenEditor).toHaveBeenCalledOnce();
	});

	it("opens assistant setup after closing the menu and hides it when MCP is disabled", async () => {
		const { user, rerender } = renderWithProviders(<Header />);
		await user.click(screen.getByRole("button", { name: "Account menu" }));
		await user.click(
			screen.getByRole("menuitem", { name: "Connect AI Assistants" }),
		);
		expect(
			screen.getByRole("dialog", { name: "Assistant Setup" }),
		).toBeVisible();
		expect(screen.queryByRole("menu")).not.toBeInTheDocument();
		runState.enabled = false;
		rerender(<Header />);
		await user.click(screen.getByRole("button", { name: "Account menu" }));
		expect(
			screen.queryByRole("menuitem", { name: "Connect AI Assistants" }),
		).not.toBeInTheDocument();
	});

	it("keeps appearance controls in the account menu at desktop and mobile widths", async () => {
		for (const mobile of [false, true]) {
			mockUseMediaQuery.mockReturnValue(mobile);
			const { user, unmount } = renderWithProviders(<Header />);
			expect(
				screen.queryByRole("button", { name: "Presentation Mode" }),
			).not.toBeInTheDocument();
			await user.click(
				screen.getByRole("button", { name: "Account menu" }),
			);
			await user.click(
				screen.getByRole("menuitem", { name: "Appearance" }),
			);
			const dark = screen.getByRole("menuitemcheckbox", {
				name: "Dark Theme",
			});
			expect(dark).toHaveAttribute("aria-checked", "true");
			dark.focus();
			await user.keyboard("{Enter}");
			expect(mockToggleTheme).toHaveBeenCalledTimes(mobile ? 2 : 1);
			await user.click(
				screen.getByRole("button", { name: "Account menu" }),
			);
			await user.click(
				screen.getByRole("menuitem", { name: "Appearance" }),
			);
			expect(
				screen.getByRole("menuitemcheckbox", {
					name: "Presentation Mode",
				}),
			).toBeVisible();
			unmount();
		}
	});

	it("shows the stored profile photo on the account button", () => {
		class LoadedImage {
			complete = true;
			naturalWidth = 4;
			addEventListener() {}
			removeEventListener() {}
			set src(_value: string) {}
		}
		vi.stubGlobal("Image", LoadedImage);
		profileState.has_avatar = true;
		profileState.dataUpdatedAt = 1_700_000_000_000;

		const { container } = renderWithProviders(<Header />);

		expect(container.querySelector("img")).toHaveAttribute(
			"src",
			"/api/profile/avatar?t=1700000000000",
		);
	});

	it("keeps desktop status visible without secondary toolbar buttons", () => {
		mockUseMediaQuery.mockImplementation((query: string) =>
			query.includes("1279px"),
		);

		renderWithProviders(<Header />);

		expect(
			screen.queryByRole("button", { name: /set up passkey/i }),
		).not.toBeInTheDocument();
		expect(screen.getByLabelText("Workspace status")).toBeVisible();
	});
});
