/**
 * Tests for FleetPage.
 *
 * The page composes hooks from `@/hooks/useAgents` (list) and
 * `@/services/agents` (fleet + per-agent stats). We mock both at module
 * scope so we can control loading / data states deterministically.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, renderWithProviders, screen, within } from "@/test-utils";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockUseAgents = vi.fn();
vi.mock("@/hooks/useAgents", () => ({
	useAgents: () => mockUseAgents(),
}));

const mockUseAgentStats = vi.fn();
const mockUseFleetStats = vi.fn();
vi.mock("@/services/agents", () => ({
	useAgentStats: (id: string | undefined) => mockUseAgentStats(id),
	useFleetStats: () => mockUseFleetStats(),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: false }),
}));

vi.mock("@/components/agents/SummaryBackfillButton", () => ({
	SummaryBackfillButton: () => null,
}));

const mockToastSuccess = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => mockToastSuccess(...args),
		error: vi.fn(),
	},
}));

// -----------------------------------------------------------------------------
// Fixtures
// -----------------------------------------------------------------------------

const fleetStats = {
	total_runs: 1234,
	avg_success_rate: 0.92,
	total_cost_7d: "8.47",
	active_agents: 5,
	needs_review: 0,
};

function makeAgent(overrides: Partial<Record<string, unknown>> = {}) {
	return {
		id: "agent-1",
		name: "Tier-1 Triage",
		description: "Triages support tickets",
		channels: ["chat"],
		is_active: true,
		access_level: "authenticated",
		organization_id: null,
		owner_user_id: null,
		created_at: "2026-04-01T00:00:00Z",
		dependency_count: 0,
		...overrides,
	};
}

const baseStats = {
	agent_id: "agent-1",
	runs_7d: 42,
	success_rate: 0.95,
	avg_duration_ms: 1500,
	total_cost_7d: "1.23",
	last_run_at: "2026-04-21T10:00:00Z",
	runs_by_day: [1, 2, 3, 4, 5, 6, 7],
	needs_review: 0,
	unreviewed: 0,
};

beforeEach(() => {
	mockUseAgents.mockReturnValue({ data: [], isLoading: false });
	mockUseFleetStats.mockReturnValue({ data: fleetStats, isLoading: false });
	mockUseAgentStats.mockReturnValue({ data: baseStats, isLoading: false });
});

async function renderPage() {
	const { FleetPage } = await import("./FleetPage");
	return renderWithProviders(<FleetPage />);
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("FleetPage — header + fleet stats", () => {
	it("renders the Agents heading", async () => {
		await renderPage();
		expect(
			screen.getByRole("heading", { name: /^agents$/i }),
		).toBeInTheDocument();
	});

	it("renders fleet stats once loaded", async () => {
		mockUseAgents.mockReturnValue({
			data: [makeAgent()],
			isLoading: false,
		});
		await renderPage();
		// FleetStats renders runs total
		expect(screen.getByText("1,234")).toBeInTheDocument();
		// And the success-rate percentage
		expect(screen.getByText("92%")).toBeInTheDocument();
	});

	it("uses compact fleet metrics on mobile while reserving full stat cards for larger screens", async () => {
		mockUseAgents.mockReturnValue({
			data: [makeAgent()],
			isLoading: false,
		});
		await renderPage();
		expect(screen.getByTestId("mobile-fleet-metrics")).toHaveClass("md:hidden");
		expect(screen.getByTestId("desktop-fleet-stats")).toHaveClass(
			"hidden",
			"md:grid",
		);
	});

	it("shows total/active subtitle from agents list", async () => {
		mockUseAgents.mockReturnValue({
			data: [
				makeAgent({ id: "a", is_active: true }),
				makeAgent({ id: "b", is_active: true }),
				makeAgent({ id: "c", is_active: false }),
			],
			isLoading: false,
		});
		await renderPage();
		expect(
			screen.getByText(/3 total · 2 active · last 7 days/i),
		).toBeInTheDocument();
	});

	it("renders the New agent button as a link to /agents/new", async () => {
		mockUseAgents.mockReturnValue({
			data: [makeAgent()],
			isLoading: false,
		});
		await renderPage();
		const link = screen.getByRole("link", { name: /new agent/i });
		expect(link).toHaveAttribute("href", "/agents/new");
	});

	it("renders the queue banner only when fleet has flagged runs", async () => {
		mockUseFleetStats.mockReturnValue({
			data: { ...fleetStats, needs_review: 4 },
			isLoading: false,
		});
		await renderPage();
		expect(
			screen.getByText(/4 flagged runs in tuning queue/i),
		).toBeInTheDocument();
	});
});

describe("FleetPage — agent cards (grid)", () => {
	it("renders one card per agent in grid view by default", async () => {
		mockUseAgents.mockReturnValue({
			data: [
				makeAgent({ id: "a", name: "Alpha" }),
				makeAgent({ id: "b", name: "Beta" }),
			],
			isLoading: false,
		});
		await renderPage();
		expect(screen.getByText("Alpha")).toBeInTheDocument();
		expect(screen.getByText("Beta")).toBeInTheDocument();
	});

	it("each card links to the agent detail page", async () => {
		mockUseAgents.mockReturnValue({
			data: [makeAgent({ id: "alpha-id", name: "Alpha" })],
			isLoading: false,
		});
		await renderPage();
		const link = screen.getByRole("link", { name: /alpha/i });
		expect(link).toHaveAttribute("href", "/agents/alpha-id");
	});
});

describe("FleetPage — agent MCP URL copy badge", () => {
	let writeText: ReturnType<typeof vi.fn>;
	let originalWriteText: typeof navigator.clipboard.writeText | undefined;

	beforeEach(() => {
		mockToastSuccess.mockClear();
		writeText = vi.fn().mockResolvedValue(undefined);
		// happy-dom's Clipboard is a real EventTarget on the Navigator prototype
		// and resists `defineProperty(navigator, "clipboard", ...)`. Patch the
		// method on the existing instance instead.
		originalWriteText = navigator.clipboard?.writeText.bind(
			navigator.clipboard,
		);
		(
			navigator.clipboard as unknown as {
				writeText: typeof writeText;
			}
		).writeText = writeText;
	});

	afterEach(() => {
		if (originalWriteText) {
			(
				navigator.clipboard as unknown as {
					writeText: typeof originalWriteText;
				}
			).writeText = originalWriteText;
		}
	});

	it("copies the agent-scoped MCP URL when the badge is clicked", async () => {
		mockUseAgents.mockReturnValue({
			data: [makeAgent({ id: "agent-xyz", name: "Alpha" })],
			isLoading: false,
		});
		await renderPage();
		fireEvent.click(screen.getByTestId("agent-mcp-copy"));
		expect(writeText).toHaveBeenCalledWith(
			`${window.location.origin}/mcp/agent-xyz`,
		);
		expect(mockToastSuccess).toHaveBeenCalledWith("Agent MCP URL copied");
	});

	it("badge click prevents the default action so the card link doesn't navigate", async () => {
		mockUseAgents.mockReturnValue({
			data: [makeAgent({ id: "agent-xyz", name: "Alpha" })],
			isLoading: false,
		});
		await renderPage();
		const badge = screen.getByTestId("agent-mcp-copy");
		const event = new MouseEvent("click", { bubbles: true, cancelable: true });
		badge.dispatchEvent(event);
		expect(writeText).toHaveBeenCalledTimes(1);
		expect(event.defaultPrevented).toBe(true);
	});
});

describe("FleetPage — search filter", () => {
	it("filters agents by name as the user types", async () => {
		mockUseAgents.mockReturnValue({
			data: [
				makeAgent({ id: "a", name: "Alpha" }),
				makeAgent({ id: "b", name: "Beta" }),
			],
			isLoading: false,
		});
		const { user } = await renderPage();
		await user.type(screen.getByLabelText(/search agents/i), "alph");
		expect(screen.getByText("Alpha")).toBeInTheDocument();
		expect(screen.queryByText("Beta")).not.toBeInTheDocument();
	});

	it("shows the empty state when nothing matches", async () => {
		mockUseAgents.mockReturnValue({
			data: [makeAgent({ id: "a", name: "Alpha" })],
			isLoading: false,
		});
		const { user } = await renderPage();
		await user.type(screen.getByLabelText(/search agents/i), "zzzzz");
		expect(
			screen.getByText(/no agents match your search/i),
		).toBeInTheDocument();
	});
});

describe("FleetPage — view toggle", () => {
	it("switches to table view when the table toggle is clicked", async () => {
		mockUseAgents.mockReturnValue({
			data: [makeAgent({ id: "a", name: "Alpha" })],
			isLoading: false,
		});
		const { user } = await renderPage();
		// In grid view, no <table> element exists.
		expect(document.querySelector("table")).toBeNull();
		await user.click(screen.getByLabelText(/table view/i));
		// After toggling, a real <table> renders.
		const table = document.querySelector("table");
		expect(table).not.toBeNull();
		// Header cells from AgentTable
		expect(within(table!).getByText(/runs \(7d\)/i)).toBeInTheDocument();
		expect(within(table!).getByText("Alpha")).toBeInTheDocument();
	});
});

describe("FleetPage — loading state", () => {
	it("renders skeletons while fleet stats and agents are loading", async () => {
		mockUseAgents.mockReturnValue({ data: undefined, isLoading: true });
		mockUseFleetStats.mockReturnValue({
			data: undefined,
			isLoading: true,
		});
		const { container } = await renderPage();
		// Skeleton renders divs with the .animate-pulse class.
		expect(
			container.querySelectorAll(".animate-pulse").length,
		).toBeGreaterThan(0);
	});
});

describe("FleetPage — empty state", () => {
	it("renders the empty state when there are no agents and no query", async () => {
		mockUseAgents.mockReturnValue({ data: [], isLoading: false });
		await renderPage();
		expect(screen.getByText(/no agents yet/i)).toBeInTheDocument();
	});
});
