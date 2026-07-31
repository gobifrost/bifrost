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
	createBuilderAppLaunch,
	deleteBuilderSolution,
	deployedRevision,
	downloadRevision,
	getRevisionDiff,
	getRevisionFile,
	getBuilderSolution,
	isPreviewStale,
	latestTurn,
	listBuilderSessions,
	listBuilderSolutions,
	listRevisions,
	listRevisionFiles,
	listTurns,
	requestPromotion,
	runBuilderTurn,
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
		build_job_id: null,
		deploy_job_id: null,
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
			jsonResponse({
				solutions: [{ id: "sol-1", slug: "todo", name: "Todo" }],
				total: 1,
				ai_configured: true,
				is_platform_admin: false,
			}),
		);

		const out = await listBuilderSolutions();

		expect(mockAuthFetch).toHaveBeenCalledWith("/api/builder/solutions", {
			signal: undefined,
		});
		expect(out.solutions).toHaveLength(1);
		expect(out.solutions[0].slug).toBe("todo");
		expect(out.ai_configured).toBe(true);
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
			jsonResponse({
				sessions: [{ id: "sess-1", conversation_id: "conv-1" }],
				total: 1,
			}),
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
			{ method: "POST", body: JSON.stringify({}), signal: undefined },
		);
		expect(out.id).toBe("sess-2");
	});
});

describe("revisions and turns", () => {
	it("lists revisions", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({ revisions: [revision()], total: 1 }),
		);

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

	it("lists files inside a revision", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({
				revision_id: "rev-3",
				files: [
					{
						path: "apps/demo/src/App.tsx",
						size_bytes: 120,
						is_text: true,
					},
				],
				total: 1,
			}),
		);

		const files = await listRevisionFiles("sol-1", "rev-3");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/revisions/rev-3/files",
			{ signal: undefined },
		);
		expect(files[0].path).toBe("apps/demo/src/App.tsx");
	});

	it("reads a source file with an encoded path query", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({
				revision_id: "rev-3",
				path: "apps/demo/src/App.tsx",
				size_bytes: 20,
				encoding: "utf-8",
				content: "export default App",
				truncated: false,
			}),
		);

		const content = await getRevisionFile(
			"sol-1",
			"rev-3",
			"apps/demo/src/App.tsx",
		);

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/revisions/rev-3/file?path=apps%2Fdemo%2Fsrc%2FApp.tsx",
			{ signal: undefined },
		);
		expect(content.content).toBe("export default App");
	});

	it("loads a revision diff against its parent by default", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({
				revision_id: "rev-3",
				against_revision_id: "rev-2",
				files: [],
				total: 0,
				additions: 0,
				deletions: 0,
			}),
		);

		const diff = await getRevisionDiff("sol-1", "rev-3");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/revisions/rev-3/diff",
			{ signal: undefined },
		);
		expect(diff.against_revision_id).toBe("rev-2");
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
			jsonResponse(
				turn({
					id: "turn-4",
					base_revision_id: "rev-3",
					output_revision_id: "rev-4",
				}),
			),
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
		expect(out.output_revision_id).toBe("rev-4");
	});

	it("lists turns", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({ turns: [turn({ status: "running" })], total: 1 }),
		);

		const out = await listTurns("sol-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/turns",
			{ signal: undefined },
		);
		expect(out[0].status).toBe("running");
	});

	it("threads an abort signal through", async () => {
		const controller = new AbortController();
		mockAuthFetch.mockResolvedValue(
			jsonResponse({ revisions: [], total: 0 }),
		);

		await listRevisions("sol-1", { signal: controller.signal });

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/revisions",
			{ signal: controller.signal },
		);
	});

	it("runs a builder turn through the specialized endpoint", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({
				turn: turn({ id: "turn-8" }),
				final_text: "Added a chart",
				tool_call_count: 2,
				revision_created: true,
			}),
		);

		const out = await runBuilderTurn("sol-1", {
			sessionId: "sess-1",
			message: "Add a chart",
		});

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/turns",
			{
				method: "POST",
				body: JSON.stringify({
					session_id: "sess-1",
					message: "Add a chart",
				}),
				signal: undefined,
			},
		);
		expect(out.revision_created).toBe(true);
	});

	it("mints an exact app-host launch URL for the requested route", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({
				launch_url: "https://apps.example.test/launch/code",
			}),
		);

		const out = await createBuilderAppLaunch(
			"sol-1",
			"app-1",
			"/reports",
		);

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/apps/app-1/launch?path=%2Freports",
			{ method: "POST", signal: undefined },
		);
		expect(out.launch_url).toContain("/launch/code");
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
		expect(error.message).toBe("App not found");
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
