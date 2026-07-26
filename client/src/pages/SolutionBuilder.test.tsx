/**
 * Tests for the builder workspace page — loading/404 states, the private badge
 * and build status, stale source-vs-preview, session gating of Undo, and the
 * revisions drawer.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { SolutionBuilder } from "./SolutionBuilder";
import {
	BuilderApiError,
	type BuilderRevision,
	type BuilderSolution,
	type BuilderTurn,
} from "@/services/builder";

vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return { ...actual, useParams: () => ({ solutionId: "sol-1" }) };
});

// ChatWindow drives itself from the chat store and hooks; stub it so the page
// test stays about builder state, not conversation plumbing.
const mockChatWindow = vi.fn();
vi.mock("@/components/chat", () => ({
	ChatWindow: (props: { conversationId?: string }) => {
		mockChatWindow(props);
		return <div data-testid="chat-window">{props.conversationId}</div>;
	},
}));

const mockGetBuilderSolution = vi.fn();
const mockListBuilderSessions = vi.fn();
const mockCreateBuilderSession = vi.fn();
const mockListRevisions = vi.fn();
const mockListTurns = vi.fn();
const mockUndoToRevision = vi.fn();
const mockDownloadRevision = vi.fn();
const mockRequestPromotion = vi.fn();

vi.mock("@/services/builder", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/builder")>(
			"@/services/builder",
		);
	return {
		...actual,
		getBuilderSolution: (...a: unknown[]) => mockGetBuilderSolution(...a),
		listBuilderSessions: (...a: unknown[]) => mockListBuilderSessions(...a),
		createBuilderSession: (...a: unknown[]) => mockCreateBuilderSession(...a),
		listRevisions: (...a: unknown[]) => mockListRevisions(...a),
		listTurns: (...a: unknown[]) => mockListTurns(...a),
		undoToRevision: (...a: unknown[]) => mockUndoToRevision(...a),
		downloadRevision: (...a: unknown[]) => mockDownloadRevision(...a),
		requestPromotion: (...a: unknown[]) => mockRequestPromotion(...a),
	};
});

function solution(overrides: Partial<BuilderSolution> = {}): BuilderSolution {
	return {
		id: "sol-1",
		slug: "expense-tracker",
		name: "Expense Tracker",
		visibility: "private",
		owner_user_id: "user-1",
		organization_id: "org-1",
		status: "active",
		promotion_status: null,
		created_at: "2026-07-25T10:00:00Z",
		updated_at: "2026-07-25T10:00:00Z",
		...overrides,
	};
}

function revision(overrides: Partial<BuilderRevision> = {}): BuilderRevision {
	return {
		id: "rev-1",
		parent_revision_id: null,
		restored_from_revision_id: null,
		source_sha256: "abc",
		size_bytes: 1024,
		summary: "Scaffold",
		created_at: "2026-07-25T10:00:00Z",
		created_by: "user-1",
		is_current: false,
		is_deployed: false,
		...overrides,
	};
}

function turn(overrides: Partial<BuilderTurn> = {}): BuilderTurn {
	return {
		id: "turn-1",
		session_id: "sess-1",
		status: "succeeded",
		error: null,
		base_revision_id: null,
		output_revision_id: null,
		created_at: "2026-07-25T10:00:00Z",
		started_at: null,
		completed_at: null,
		...overrides,
	};
}

const SESSION = {
	id: "sess-1",
	solution_id: "sol-1",
	conversation_id: "conv-1",
	user_id: "user-1",
	created_at: "2026-07-25T10:00:00Z",
};

beforeEach(() => {
	vi.clearAllMocks();
	mockGetBuilderSolution.mockResolvedValue(solution());
	mockListBuilderSessions.mockResolvedValue([SESSION]);
	mockListRevisions.mockResolvedValue([revision({ is_current: true, is_deployed: true })]);
	mockListTurns.mockResolvedValue([turn()]);
});

describe("load states", () => {
	it("shows a skeleton while the solution loads", () => {
		mockGetBuilderSolution.mockReturnValue(new Promise(() => {}));

		renderWithProviders(<SolutionBuilder />);

		expect(screen.getByTestId("builder-loading")).toBeInTheDocument();
	});

	it("renders a not-found state for a 404, never a permissions error", async () => {
		mockGetBuilderSolution.mockRejectedValue(new BuilderApiError(404, "nope"));

		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("builder-error")).toBeInTheDocument();
		expect(screen.getByText(/solution not found/i)).toBeInTheDocument();
		expect(screen.queryByText(/permission|forbidden/i)).not.toBeInTheDocument();
	});

	it("renders a generic error for other failures", async () => {
		mockGetBuilderSolution.mockRejectedValue(
			new BuilderApiError(500, "Builder service unavailable"),
		);

		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("builder-error")).toBeInTheDocument();
		expect(
			screen.getByText(/builder service unavailable/i),
		).toBeInTheDocument();
	});
});

describe("top bar", () => {
	it("shows the name, slug, and Private badge", async () => {
		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByText("Expense Tracker")).toBeInTheDocument();
		expect(screen.getByText("expense-tracker")).toBeInTheDocument();
		expect(screen.getByText("Private")).toBeInTheDocument();
	});

	it("omits the Private badge for a shared solution", async () => {
		mockGetBuilderSolution.mockResolvedValue(
			solution({ visibility: "shared" }),
		);

		renderWithProviders(<SolutionBuilder />);

		await screen.findByText("Expense Tracker");
		expect(screen.queryByText("Private")).not.toBeInTheDocument();
	});

	it("reflects a running turn in the build status", async () => {
		mockListTurns.mockResolvedValue([turn({ status: "running" })]);

		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("build-status")).toHaveTextContent(
			"Building",
		);
	});

	it("reflects a failed turn", async () => {
		mockListTurns.mockResolvedValue([turn({ status: "failed" })]);

		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("build-status")).toHaveTextContent(
			"Build failed",
		);
	});

	it("shows Idle when there have been no turns", async () => {
		mockListTurns.mockResolvedValue([]);

		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("build-status")).toHaveTextContent("Idle");
	});

	it("requests promotion and then disables the button", async () => {
		mockRequestPromotion.mockResolvedValue(
			solution({ promotion_status: "requested" }),
		);

		const { user } = renderWithProviders(<SolutionBuilder />);

		await user.click(
			await screen.findByRole("button", { name: /request promotion/i }),
		);

		await waitFor(() => expect(mockRequestPromotion).toHaveBeenCalledWith("sol-1"));
	});

	it("shows promotion as already requested", async () => {
		mockGetBuilderSolution.mockResolvedValue(
			solution({ promotion_status: "requested" }),
		);

		renderWithProviders(<SolutionBuilder />);

		expect(
			await screen.findByRole("button", { name: /promotion requested/i }),
		).toBeDisabled();
	});

	it("disables Open app when no app origin is configured", async () => {
		renderWithProviders(<SolutionBuilder />);

		expect(
			await screen.findByRole("button", { name: /open app/i }),
		).toBeDisabled();
	});
});

describe("source vs preview", () => {
	it("flags stale preview when the current revision is not deployed", async () => {
		mockListRevisions.mockResolvedValue([
			revision({ id: "rev-3", is_current: true }),
			revision({ id: "rev-2", is_deployed: true }),
		]);

		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("source-ahead-note")).toBeInTheDocument();
		expect(screen.getByTestId("stale-preview-badge")).toBeInTheDocument();
	});

	it("does not flag stale when source and preview agree", async () => {
		renderWithProviders(<SolutionBuilder />);

		await screen.findByTestId("build-status");
		expect(screen.queryByTestId("source-ahead-note")).not.toBeInTheDocument();
	});
});

describe("chat sessions", () => {
	it("hands the session conversation id to the chat window", async () => {
		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("chat-window")).toHaveTextContent("conv-1");
		expect(mockChatWindow).toHaveBeenCalledWith(
			expect.objectContaining({ conversationId: "conv-1" }),
		);
	});

	it("offers to start a session when there are none", async () => {
		mockListBuilderSessions.mockResolvedValue([]);

		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("no-session")).toBeInTheDocument();
		expect(screen.queryByTestId("chat-window")).not.toBeInTheDocument();
	});

	it("creates a session on demand", async () => {
		mockListBuilderSessions.mockResolvedValue([]);
		mockCreateBuilderSession.mockResolvedValue(SESSION);

		const { user } = renderWithProviders(<SolutionBuilder />);

		await user.click(await screen.findByRole("button", { name: /start session/i }));

		await waitFor(() =>
			expect(mockCreateBuilderSession).toHaveBeenCalledWith("sol-1"),
		);
	});
});

describe("revisions drawer", () => {
	it("lists revisions with their badges", async () => {
		mockListRevisions.mockResolvedValue([
			revision({ id: "rev-3", is_current: true, summary: "Latest change" }),
			revision({ id: "rev-2", is_deployed: true, summary: "Deployed build" }),
		]);

		renderWithProviders(<SolutionBuilder />);

		expect(await screen.findByTestId("revision-list")).toBeInTheDocument();
		expect(screen.getByText("Latest change")).toBeInTheDocument();
		expect(screen.getByText("Source")).toBeInTheDocument();
		expect(screen.getByText("Preview")).toBeInTheDocument();
	});

	it("collapses and expands", async () => {
		const { user } = renderWithProviders(<SolutionBuilder />);

		await screen.findByTestId("revision-list");

		await user.click(screen.getByRole("button", { name: /files and revisions/i }));

		expect(screen.queryByTestId("revision-list")).not.toBeInTheDocument();
	});

	it("undoes to a chosen revision using the active session", async () => {
		mockListRevisions.mockResolvedValue([
			revision({ id: "rev-3", is_current: true }),
			revision({ id: "rev-2", summary: "Earlier" }),
		]);
		mockUndoToRevision.mockResolvedValue(revision({ id: "rev-4" }));

		const { user } = renderWithProviders(<SolutionBuilder />);

		await user.click(
			await screen.findByRole("button", { name: /undo to revision rev-2/i }),
		);
		await user.click(screen.getByRole("button", { name: /^restore$/i }));

		await waitFor(() =>
			expect(mockUndoToRevision).toHaveBeenCalledWith("sol-1", {
				toRevisionId: "rev-2",
				sessionId: "sess-1",
			}),
		);
	});

	it("disables per-revision undo when no session exists", async () => {
		mockListBuilderSessions.mockResolvedValue([]);
		mockListRevisions.mockResolvedValue([revision({ id: "rev-2" })]);

		renderWithProviders(<SolutionBuilder />);

		expect(
			await screen.findByRole("button", { name: /undo to revision rev-2/i }),
		).toBeDisabled();
	});

	it("downloads a revision", async () => {
		mockDownloadRevision.mockResolvedValue({
			blob: new Blob(["z"]),
			filename: "source.zip",
		});

		const { user } = renderWithProviders(<SolutionBuilder />);

		await user.click(
			await screen.findByRole("button", { name: /download revision rev-1/i }),
		);

		await waitFor(() =>
			expect(mockDownloadRevision).toHaveBeenCalledWith("sol-1", "rev-1"),
		);
	});

	it("surfaces an action failure", async () => {
		mockDownloadRevision.mockRejectedValue(new Error("Storage unavailable"));

		const { user } = renderWithProviders(<SolutionBuilder />);

		await user.click(
			await screen.findByRole("button", { name: /download revision rev-1/i }),
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Storage unavailable",
		);
	});
});
