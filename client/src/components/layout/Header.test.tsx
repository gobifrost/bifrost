import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

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
		isPlatformAdmin: true,
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
