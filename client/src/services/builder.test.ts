import { beforeEach, describe, expect, it, vi } from "vitest";

const mockAuthFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

import {
	BuilderApiError,
	BuilderRevision,
	BuilderTurn,
	createBuilderSession,
	createBuilderSolution,
	currentRevision,
	deleteBuilderSolution,
	deployedRevision,
	downloadRevision,
	getBuilderSolution,
	isPreviewStale,
	latestTurn,
	listBuilderSessions,
	listBuilderSolutions,
	listRevisions,
	listTurns,
	requestPromotion,
	undoToRevision,
} from "./builder";

function jsonResponse(body: unknown, status = 200) {
	return {
		ok: status >= 200 && status < 300,
		status,
		headers: new Headers(),
		json: () => Promise.resolve(body),
	};
}

function errorResponse(status: number, body: unknown = {}) {
	return {
		ok: false,
		status,
		headers: new Headers(),
		json: () => Promise.resolve(body),
	};
}

function revision(overrides: Partial<BuilderRevision> = {}): BuilderRevision {
	return {
		id: "rev-1",
		parent_revision_id: null,
		restored_from_revision_id: null,
		source_sha256: "abc",
		size_bytes: 1024,
		summary: "Initial scaffold",
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

beforeEach(() => {
	mockAuthFetch.mockReset();
});

describe("builder solutions", () => {
	it("lists private solutions", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse([{ id: "sol-1", slug: "todo", name: "Todo" }]),
		);

		const out = await listBuilderSolutions();

		expect(mockAuthFetch).toHaveBeenCalledWith("/api/builder/solutions", {
			signal: undefined,
		});
		expect(out).toHaveLength(1);
		expect(out[0].slug).toBe("todo");
	});

	it("creates a solution with a slug and name body", async () => {
		mockAuthFetch.mockResolvedValue(jsonResponse({ id: "sol-2" }));

		const out = await createBuilderSolution({ slug: "notes", name: "Notes" });

		expect(mockAuthFetch).toHaveBeenCalledWith("/api/builder/solutions", {
			method: "POST",
			body: JSON.stringify({ slug: "notes", name: "Notes" }),
			signal: undefined,
		});
		expect(out.id).toBe("sol-2");
	});

	it("gets a solution by id", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({ id: "sol-1", visibility: "private" }),
		);

		const out = await getBuilderSolution("sol-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1",
			{ signal: undefined },
		);
		expect(out.visibility).toBe("private");
	});

	it("deletes a solution and returns nothing", async () => {
		mockAuthFetch.mockResolvedValue({ ok: true, status: 204 });

		await expect(deleteBuilderSolution("sol-1")).resolves.toBeUndefined();

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1",
			{ method: "DELETE", signal: undefined },
		);
	});

	it("requests promotion", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({ id: "sol-1", promotion_status: "requested" }),
		);

		const out = await requestPromotion("sol-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/promotion-request",
			{ method: "POST", signal: undefined },
		);
		expect(out.promotion_status).toBe("requested");
	});
});

describe("builder sessions", () => {
	it("lists sessions", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse([{ id: "sess-1", conversation_id: "conv-1" }]),
		);

		const out = await listBuilderSessions("sol-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/sessions",
			{ signal: undefined },
		);
		expect(out[0].conversation_id).toBe("conv-1");
	});

	it("creates a session", async () => {
		mockAuthFetch.mockResolvedValue(jsonResponse({ id: "sess-2" }));

		const out = await createBuilderSession("sol-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/sessions",
			{ method: "POST", signal: undefined },
		);
		expect(out.id).toBe("sess-2");
	});
});

describe("revisions and turns", () => {
	it("lists revisions", async () => {
		mockAuthFetch.mockResolvedValue(jsonResponse([revision()]));

		const out = await listRevisions("sol-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/revisions",
			{ signal: undefined },
		);
		expect(out[0].source_sha256).toBe("abc");
	});

	it("downloads a revision and reads the filename from Content-Disposition", async () => {
		const blob = new Blob(["zipbytes"], { type: "application/zip" });
		mockAuthFetch.mockResolvedValue({
			ok: true,
			status: 200,
			headers: new Headers({
				"Content-Disposition": 'attachment; filename="todo-rev-3.zip"',
			}),
			blob: () => Promise.resolve(blob),
		});

		const out = await downloadRevision("sol-1", "rev-3");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/revisions/rev-3/download",
			{ signal: undefined },
		);
		expect(out).toEqual({ blob, filename: "todo-rev-3.zip" });
	});

	it("falls back to a derived filename when the header is absent", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			status: 200,
			headers: new Headers(),
			blob: () => Promise.resolve(new Blob([])),
		});

		const out = await downloadRevision("sol-1", "rev-3");

		expect(out.filename).toBe("solution-sol-1-source.zip");
	});

	it("posts undo with snake_cased body fields", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse(revision({ id: "rev-4", restored_from_revision_id: "rev-2" })),
		);

		const out = await undoToRevision("sol-1", {
			toRevisionId: "rev-2",
			sessionId: "sess-1",
		});

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/undo",
			{
				method: "POST",
				body: JSON.stringify({
					to_revision_id: "rev-2",
					session_id: "sess-1",
				}),
				signal: undefined,
			},
		);
		expect(out.restored_from_revision_id).toBe("rev-2");
	});

	it("lists turns", async () => {
		mockAuthFetch.mockResolvedValue(jsonResponse([turn({ status: "running" })]));

		const out = await listTurns("sol-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/turns",
			{ signal: undefined },
		);
		expect(out[0].status).toBe("running");
	});

	it("threads an abort signal through", async () => {
		const controller = new AbortController();
		mockAuthFetch.mockResolvedValue(jsonResponse([]));

		await listRevisions("sol-1", { signal: controller.signal });

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/revisions",
			{ signal: controller.signal },
		);
	});
});

describe("error mapping", () => {
	it("maps 403 to a forbidden BuilderApiError", async () => {
		mockAuthFetch.mockResolvedValue(errorResponse(403));

		const error = await listBuilderSolutions().catch((e) => e);

		expect(error).toBeInstanceOf(BuilderApiError);
		expect(error.status).toBe(403);
		expect(error.isForbidden).toBe(true);
		expect(error.isNotFound).toBe(false);
	});

	it("maps 404 to a not-found BuilderApiError without a permissions message", async () => {
		mockAuthFetch.mockResolvedValue(errorResponse(404));

		const error = await getBuilderSolution("sol-x").catch((e) => e);

		expect(error).toBeInstanceOf(BuilderApiError);
		expect(error.isNotFound).toBe(true);
		expect(error.message).toBe("Solution not found");
		expect(error.message).not.toMatch(/permission|forbidden|access/i);
	});

	it("surfaces the server detail for other failures", async () => {
		mockAuthFetch.mockResolvedValue(
			errorResponse(409, { detail: "A turn is already running" }),
		);

		const error = await undoToRevision("sol-1", {
			toRevisionId: "rev-2",
			sessionId: "sess-1",
		}).catch((e) => e);

		expect(error.status).toBe(409);
		expect(error.message).toBe("A turn is already running");
	});

	it("falls back to a default message when the body is not JSON", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: false,
			status: 500,
			headers: new Headers(),
			json: () => Promise.reject(new Error("not json")),
		});

		const error = await listTurns("sol-1").catch((e) => e);

		expect(error.message).toBe("Failed to list builder turns");
	});

	it("maps errors on downloads too", async () => {
		mockAuthFetch.mockResolvedValue(errorResponse(404));

		const error = await downloadRevision("sol-1", "rev-9").catch((e) => e);

		expect(error).toBeInstanceOf(BuilderApiError);
		expect(error.isNotFound).toBe(true);
	});

	it("maps errors on delete", async () => {
		mockAuthFetch.mockResolvedValue(errorResponse(403));

		const error = await deleteBuilderSolution("sol-1").catch((e) => e);

		expect(error.isForbidden).toBe(true);
	});
});

describe("revision selectors", () => {
	it("finds the current and deployed revisions", () => {
		const revisions = [
			revision({ id: "rev-3", is_current: true }),
			revision({ id: "rev-2", is_deployed: true }),
			revision({ id: "rev-1" }),
		];

		expect(currentRevision(revisions)?.id).toBe("rev-3");
		expect(deployedRevision(revisions)?.id).toBe("rev-2");
	});

	it("returns null when there is no current or deployed revision", () => {
		expect(currentRevision([])).toBeNull();
		expect(deployedRevision([revision()])).toBeNull();
	});

	it("reports a stale preview when source is ahead of the deployed revision", () => {
		const revisions = [
			revision({ id: "rev-3", is_current: true }),
			revision({ id: "rev-2", is_deployed: true }),
		];

		expect(isPreviewStale(revisions)).toBe(true);
	});

	it("is not stale when the current revision is the deployed one", () => {
		const revisions = [
			revision({ id: "rev-2", is_current: true, is_deployed: true }),
		];

		expect(isPreviewStale(revisions)).toBe(false);
	});

	it("is not stale before anything has deployed", () => {
		expect(isPreviewStale([revision({ id: "rev-1", is_current: true })])).toBe(
			false,
		);
	});

	it("picks the newest turn by creation time regardless of order", () => {
		const turns = [
			turn({ id: "turn-1", created_at: "2026-07-25T10:00:00Z" }),
			turn({ id: "turn-3", created_at: "2026-07-25T12:00:00Z" }),
			turn({ id: "turn-2", created_at: "2026-07-25T11:00:00Z" }),
		];

		expect(latestTurn(turns)?.id).toBe("turn-3");
		expect(latestTurn([])).toBeNull();
	});
});
