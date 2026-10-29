import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const authorizationState = vi.hoisted(() => ({
	settingsRead: false,
	settingsReadWrite: false,
	repositoryReadWrite: false,
}));
const mockCanAt = vi.hoisted(() => vi.fn());
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
			has_avatar: false,
		},
		dataUpdatedAt: 0,
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

vi.mock("@/components/theme-toggle", () => ({
	ThemeToggle: () => <button type="button">Toggle theme</button>,
}));

vi.mock("@/components/layout/BifrostRunMenu", () => ({
	BifrostRunMenu: () => <button type="button">Connect AI assistants</button>,
}));

vi.mock("@/components/layout/HeaderStatusIndicators", () => ({
	HeaderStatusIndicators: () => <span>Workspace status indicator</span>,
}));

import { Header } from "./Header";

beforeEach(() => {
	vi.clearAllMocks();
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

	it("shows Help for an exact Global settings.read holder", () => {
		const view = renderWithProviders(<Header />);
		expect(screen.getByRole("button", { name: "Help" })).toBeVisible();
		expect(mockCanAt).toHaveBeenCalledWith("settings.read", {
			kind: "global",
		});
		authorizationState.settingsRead = false;
		view.rerender(<Header />);
		expect(
			screen.queryByRole("button", { name: "Help" }),
		).not.toBeInTheDocument();
	});

	it("shows the shell only to an exact Global repository writer", () => {
		const view = renderWithProviders(<Header />);
		expect(
			screen.queryByRole("button", { name: /shell/i }),
		).not.toBeInTheDocument();

		authorizationState.repositoryReadWrite = true;
		view.rerender(<Header />);
		expect(screen.getByRole("button", { name: /shell/i })).toBeVisible();
	});

	it("retains secondary header content outside the mobile breakpoint", () => {
		mockUseMediaQuery.mockImplementation((query: string) =>
			query.includes("1279px"),
		);

		renderWithProviders(<Header />);

		expect(
			screen.getByRole("button", { name: /set up passkey/i }),
		).toBeVisible();
		expect(screen.getByLabelText("Workspace status")).toBeVisible();
	});
});
