import { beforeEach, describe, expect, it, vi } from "vitest";

const mockAuthFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

import {
	BuilderApiError,
	BuilderRevision,
	BuilderTurn,
	applyGlobalWorkspace,
	createBuilderSession,
	createBuilderSolution,
	currentRevision,
	createBuilderAppLaunch,
	deleteBuilderSolution,
	deployedRevision,
	discardGlobalOperationChange,
	downloadRevision,
	getRevisionDiff,
	getRevisionFile,
	getBuilderSolution,
	getGlobalWorkspace,
	isPreviewStale,
	latestTurn,
	listBuilderSessions,
	listBuilderGrantableRoles,
	listBuilderRoleGrants,
	listBuilderSolutions,
	listBuilderTargets,
	listGlobalOperationChanges,
	listRevisions,
	listRevisionFiles,
	listTurns,
	requestPromotion,
	removeBuilderRoleGrant,
	rollbackGlobalWorkspace,
	runBuilderTurn,
	saveBuilderRoleGrant,
	validateGlobalWorkspace,
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
		checkpoint_available: overrides.checkpoint_available ?? false,
	};
}

beforeEach(() => {
	mockAuthFetch.mockReset();
});

describe("global workspace", () => {
	it("loads, validates, applies, and rolls back through explicit admin routes", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(
				jsonResponse({ exists: true, solution_id: "global-1" }),
			)
			.mockResolvedValueOnce(
				jsonResponse({ revision_id: "rev-2", valid: true, errors: [] }),
			)
			.mockResolvedValueOnce(
				jsonResponse({
					job_id: "job-apply",
					notification_id: "notif-apply",
					status: "waiting",
					reused: false,
				}),
			)
			.mockResolvedValueOnce(
				jsonResponse({
					job_id: "job-rollback",
					notification_id: "notif-rollback",
					status: "waiting",
					reused: false,
				}),
			);

		await expect(getGlobalWorkspace()).resolves.toMatchObject({
			exists: true,
			solution_id: "global-1",
		});
		await expect(validateGlobalWorkspace()).resolves.toMatchObject({
			valid: true,
		});
		await expect(applyGlobalWorkspace()).resolves.toMatchObject({
			job_id: "job-apply",
		});
		await expect(rollbackGlobalWorkspace()).resolves.toMatchObject({
			job_id: "job-rollback",
		});

		expect(mockAuthFetch.mock.calls.map(([url]) => url)).toEqual([
			"/api/builder/solutions/global-workspace",
			"/api/builder/solutions/global-workspace/validate",
			"/api/builder/solutions/global-workspace/apply",
			"/api/builder/solutions/global-workspace/rollback",
		]);
		for (const [, init] of mockAuthFetch.mock.calls) {
			expect((init.headers as Headers).get("X-Bifrost-Boundary")).toBe(
				"platform",
			);
		}
	});

	it("lists and discards Global operation changes with platform boundary", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(
				jsonResponse({
					changes: [
						{
							id: "change-1",
							operation_id: "agents.create",
							resource_type: "agent",
							resource_id: null,
							state: "staged",
							validation_errors: [],
							before_state: null,
							after_state: { name: "Global Agent" },
						},
					],
				}),
			)
			.mockResolvedValueOnce(
				jsonResponse({
					id: "change-1",
					operation_id: "agents.create",
					resource_type: "agent",
					resource_id: null,
					state: "discarded",
					validation_errors: [],
					before_state: null,
					after_state: { name: "Global Agent" },
				}),
			);

		await expect(listGlobalOperationChanges()).resolves.toMatchObject({
			changes: [{ id: "change-1" }],
		});
		await expect(
			discardGlobalOperationChange("change-1"),
		).resolves.toMatchObject({ state: "discarded" });

		expect(mockAuthFetch.mock.calls.map(([url]) => url)).toEqual([
			"/api/builder/solutions/global-workspace/operations",
			"/api/builder/solutions/global-workspace/operations/change-1",
		]);
		expect(mockAuthFetch.mock.calls.map(([, init]) => init.method ?? "GET")).toEqual([
			"GET",
			"DELETE",
		]);
		for (const [, init] of mockAuthFetch.mock.calls) {
			expect((init.headers as Headers).get("X-Bifrost-Boundary")).toBe(
				"platform",
			);
		}
	});
});

describe("builder solutions", () => {
	it("discovers selectable Builder boundaries before choosing a target", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({
				organizations: [
					{
						id: "org-1",
						name: "Customer",
						can_read: true,
						can_execute: true,
						can_build_resources: true,
					},
				],
			}),
		);

		const result = await listBuilderTargets();

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/targets",
			{ signal: undefined },
		);
		expect(result.organizations?.[0].id).toBe("org-1");
	});

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

	it("serializes support-catalog filters and pagination", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({ solutions: [], total: 0, view: "all" }),
		);

		await listBuilderSolutions({
			view: "all",
			organizationId: "org-2",
			ownerUserId: "user-3",
			search: "  inventory  ",
			limit: 50,
			offset: 100,
		});

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions?view=all&organization_id=org-2&owner_user_id=user-3&search=inventory&limit=50&offset=100",
			expect.objectContaining({ signal: undefined }),
		);
		const init = mockAuthFetch.mock.calls[0][1];
		expect((init.headers as Headers).get("X-Bifrost-Boundary")).toBe(
			"managed_organizations",
		);
	});

	it("creates a solution with a slug and name body", async () => {
		mockAuthFetch.mockResolvedValue(jsonResponse({ id: "sol-2" }));

		const out = await createBuilderSolution({
			slug: "notes",
			name: "Notes",
			target_kind: "solution",
		});

		expect(mockAuthFetch).toHaveBeenCalledWith("/api/builder/solutions", {
			method: "POST",
			body: JSON.stringify({
				slug: "notes",
				name: "Notes",
				target_kind: "solution",
			}),
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

	it("sends an explicit organization boundary for support work", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({ id: "sol-1", visibility: "private" }),
		);

		await getBuilderSolution("sol-1", {
			boundary: "organization:11111111-1111-1111-1111-111111111111",
		});

		const init = mockAuthFetch.mock.calls[0][1];
		expect((init.headers as Headers).get("X-Bifrost-Boundary")).toBe(
			"organization:11111111-1111-1111-1111-111111111111",
		);
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

describe("builder Role access", () => {
	it("loads only Roles that may be assigned to resources", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse([
				{ id: "role-1", name: "Reviewer", assignable_to_resources: true },
				{ id: "role-2", name: "Platform Builder", assignable_to_resources: false },
			]),
		);

		const roles = await listBuilderGrantableRoles({
			boundary: "organization:11111111-1111-1111-1111-111111111111",
		});

		expect(roles.map((role) => role.id)).toEqual(["role-1"]);
		const init = mockAuthFetch.mock.calls[0][1];
		expect((init.headers as Headers).get("X-Bifrost-Boundary")).toBe(
			"organization:11111111-1111-1111-1111-111111111111",
		);
	});

	it("lists, saves, and removes Solution Role grants", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(jsonResponse([{ id: "grant-1", role_id: "role-1" }]))
			.mockResolvedValueOnce(jsonResponse({ id: "grant-1", role_id: "role-1", access: "view" }))
			.mockResolvedValueOnce({ ok: true, status: 204 });

		await expect(listBuilderRoleGrants("sol-1")).resolves.toHaveLength(1);
		await expect(
			saveBuilderRoleGrant("sol-1", { role_id: "role-1", access: "view" }),
		).resolves.toMatchObject({ access: "view" });
		await expect(
			removeBuilderRoleGrant("sol-1", "role-1"),
		).resolves.toBeUndefined();

		expect(mockAuthFetch.mock.calls.map(([url]) => url)).toEqual([
			"/api/builder/solutions/sol-1/role-grants",
			"/api/builder/solutions/sol-1/role-grants",
			"/api/builder/solutions/sol-1/role-grants/role-1",
		]);
		expect(mockAuthFetch.mock.calls[1][1]).toMatchObject({
			method: "PUT",
			body: JSON.stringify({
				solution_id: "sol-1",
				role_id: "role-1",
				access: "view",
			}),
		});
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
				job_id: "job-8",
				status: "queued",
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
					attachment_ids: [],
				}),
				signal: undefined,
			},
		);
		expect(out.job_id).toBe("job-8");
		expect(out.status).toBe("queued");
	});

	it("explicitly resumes an isolated failed-turn checkpoint", async () => {
		mockAuthFetch.mockResolvedValue(
			jsonResponse({
				turn: turn({ id: "turn-9" }),
				job_id: "job-9",
				status: "queued",
			}),
		);

		await runBuilderTurn("sol-1", {
			sessionId: "sess-1",
			message: "Continue from the saved checkpoint.",
			resumeFromTurnId: "turn-8",
		});

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/builder/solutions/sol-1/turns",
			{
				method: "POST",
				body: JSON.stringify({
					session_id: "sess-1",
					message: "Continue from the saved checkpoint.",
					attachment_ids: [],
					resume_from_turn_id: "turn-8",
				}),
				signal: undefined,
			},
		);
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
