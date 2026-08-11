/**
 * Private Solution Builder service.
 *
 * Wraps the `/api/builder/*` control plane: private Solutions, builder chat
 * sessions, source revisions, undo, and agent turns.
 *
 */

import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type SolutionVisibility = "private" | "shared";
export type BuilderSolution = components["schemas"]["PrivateSolutionDTO"];
export type BuilderProject = components["schemas"]["BuilderProjectDTO"];
export type BuilderSession = components["schemas"]["BuilderSessionDTO"];
export type BuilderRevision = components["schemas"]["SourceRevisionDTO"];
export type BuilderRevisionFile = components["schemas"]["RevisionFileDTO"];
export type BuilderRevisionFileContent =
	components["schemas"]["RevisionFileContentDTO"];
export type BuilderRevisionDiffFile =
	components["schemas"]["RevisionDiffFileDTO"];
export type BuilderRevisionDiff = components["schemas"]["RevisionDiffDTO"];
export type BuilderCollaborator =
	components["schemas"]["BuilderCollaboratorDTO"];
export type BuilderBlocker = components["schemas"]["SandboxRunnerBlocker"];
export type GlobalWorkspaceStatus =
	components["schemas"]["GlobalWorkspaceStatusDTO"];
export type GlobalWorkspaceValidation =
	components["schemas"]["GlobalWorkspaceValidationDTO"];
export type GlobalWorkspaceApply =
	components["schemas"]["GlobalWorkspaceApplyDTO"];

export type BuilderTurnStatus =
	| "queued"
	| "running"
	| "succeeded"
	| "failed"
	| "cancelled";

export type BuilderTurn = Omit<
	components["schemas"]["BuilderTurnDTO"],
	"status"
> & { status: BuilderTurnStatus };
export type BuilderTurnResult = Omit<
	components["schemas"]["RunTurnResponse"],
	"turn"
> & { turn: BuilderTurn };
export type CreateBuilderSolutionRequest =
	components["schemas"]["PrivateSolutionCreate"];

export interface BuilderLaunch {
	launch_url: string;
}

export interface BuilderDownload {
	blob: Blob;
	filename: string;
}

export type BuilderSolutionsList =
	components["schemas"]["PrivateSolutionsList"];

interface RequestOptions {
	signal?: AbortSignal;
}

export interface BuilderSolutionFilters {
	view?: "mine" | "all";
	organizationId?: string | null;
	ownerUserId?: string | null;
	search?: string;
}

/**
 * Error carrying the HTTP status so callers can distinguish the two states the
 * builder treats specially: 403 (no `solutions.build` capability — hide the
 * feature) and 404 (not the owner — render as "not found", never as a
 * permissions failure).
 */
export class BuilderApiError extends Error {
	readonly status: number;

	constructor(status: number, message: string) {
		super(message);
		this.name = "BuilderApiError";
		this.status = status;
	}

	/** The caller lacks the `solutions.build` capability. Hide builder entry points. */
	get isForbidden(): boolean {
		return this.status === 403;
	}

	/** Not the owner, or no such Solution. Present as "not found". */
	get isNotFound(): boolean {
		return this.status === 404;
	}
}

const BASE = "/api/builder/solutions";

async function errorFrom(
	response: Response,
	fallback: string,
): Promise<BuilderApiError> {
	if (response.status === 404) {
		return new BuilderApiError(404, "App not found");
	}
	if (response.status === 403) {
		return new BuilderApiError(
			403,
			"You do not have access to the Solution builder",
		);
	}
	const body = await response.json().catch(() => null);
	const rawDetail =
		body && typeof body === "object" && "detail" in body
			? body.detail
			: null;
	const detail =
		typeof rawDetail === "string"
			? rawDetail
			: rawDetail &&
				  typeof rawDetail === "object" &&
				  "message" in rawDetail &&
				  typeof rawDetail.message === "string"
				? rawDetail.message
				: fallback;
	return new BuilderApiError(response.status, detail);
}

async function requestJson<T>(
	url: string,
	fallback: string,
	init: RequestInit = {},
): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) {
		throw await errorFrom(response, fallback);
	}
	return (await response.json()) as T;
}

function filenameFrom(response: Response, fallback: string): string {
	const disposition = response.headers.get("Content-Disposition") ?? "";
	const match = /filename="([^"]+)"/.exec(disposition);
	return match?.[1] ?? fallback;
}

export async function listBuilderSolutions(
	options: RequestOptions & BuilderSolutionFilters = {},
): Promise<BuilderSolutionsList> {
	const query = new URLSearchParams();
	if (options.view) query.set("view", options.view);
	if (options.organizationId) {
		query.set("organization_id", options.organizationId);
	}
	if (options.ownerUserId) query.set("owner_user_id", options.ownerUserId);
	if (options.search?.trim()) query.set("search", options.search.trim());
	const suffix = query.size > 0 ? `?${query.toString()}` : "";
	return requestJson<BuilderSolutionsList>(
		`${BASE}${suffix}`,
		"Failed to list builder solutions",
		{ signal: options.signal },
	);
}

export async function getGlobalWorkspace(
	options: RequestOptions = {},
): Promise<GlobalWorkspaceStatus> {
	return requestJson<GlobalWorkspaceStatus>(
		`${BASE}/global-workspace`,
		"Failed to load the Global Workspace",
		{ signal: options.signal },
	);
}

export async function ensureGlobalWorkspace(
	options: RequestOptions = {},
): Promise<GlobalWorkspaceStatus> {
	return requestJson<GlobalWorkspaceStatus>(
		`${BASE}/global-workspace`,
		"Failed to open the Global Workspace",
		{ method: "POST", signal: options.signal },
	);
}

export async function refreshGlobalWorkspace(
	options: RequestOptions = {},
): Promise<GlobalWorkspaceStatus> {
	return requestJson<GlobalWorkspaceStatus>(
		`${BASE}/global-workspace/refresh`,
		"Failed to refresh the Global Workspace",
		{ method: "POST", signal: options.signal },
	);
}

export async function validateGlobalWorkspace(
	options: RequestOptions = {},
): Promise<GlobalWorkspaceValidation> {
	return requestJson<GlobalWorkspaceValidation>(
		`${BASE}/global-workspace/validate`,
		"Failed to validate the Global Workspace proposal",
		{ method: "POST", signal: options.signal },
	);
}

export async function applyGlobalWorkspace(
	options: RequestOptions = {},
): Promise<GlobalWorkspaceApply> {
	return requestJson<GlobalWorkspaceApply>(
		`${BASE}/global-workspace/apply`,
		"Failed to apply the Global Workspace proposal",
		{ method: "POST", signal: options.signal },
	);
}

export async function rollbackGlobalWorkspace(
	options: RequestOptions = {},
): Promise<GlobalWorkspaceApply> {
	return requestJson<GlobalWorkspaceApply>(
		`${BASE}/global-workspace/rollback`,
		"Failed to roll back the Global Workspace",
		{ method: "POST", signal: options.signal },
	);
}

export async function listBuilderCollaborators(
	solutionId: string,
	options: RequestOptions = {},
): Promise<BuilderCollaborator[]> {
	const result = await requestJson<
		components["schemas"]["BuilderCollaboratorsList"]
	>(`${BASE}/${solutionId}/collaborators`, "Failed to load collaborators", {
		signal: options.signal,
	});
	return result.collaborators;
}

export async function saveBuilderCollaborator(
	solutionId: string,
	request: components["schemas"]["BuilderCollaboratorUpsert"],
	options: RequestOptions = {},
): Promise<BuilderCollaborator> {
	return requestJson<BuilderCollaborator>(
		`${BASE}/${solutionId}/collaborators`,
		"Failed to save collaborator",
		{
			method: "PUT",
			body: JSON.stringify(request),
			signal: options.signal,
		},
	);
}

export async function removeBuilderCollaborator(
	solutionId: string,
	userId: string,
	options: RequestOptions = {},
): Promise<void> {
	const response = await authFetch(
		`${BASE}/${solutionId}/collaborators/${userId}`,
		{ method: "DELETE", signal: options.signal },
	);
	if (!response.ok) {
		throw await errorFrom(response, "Failed to remove collaborator");
	}
}

export async function createBuilderSolution(
	request: CreateBuilderSolutionRequest,
	options: RequestOptions = {},
): Promise<BuilderSolution> {
	return requestJson<BuilderSolution>(BASE, "Failed to create app", {
		method: "POST",
		body: JSON.stringify(request),
		signal: options.signal,
	});
}

export async function getBuilderSolution(
	solutionId: string,
	options: RequestOptions = {},
): Promise<BuilderSolution> {
	return requestJson<BuilderSolution>(
		`${BASE}/${solutionId}`,
		"Failed to load solution",
		{ signal: options.signal },
	);
}

export async function deleteBuilderSolution(
	solutionId: string,
	options: RequestOptions = {},
): Promise<void> {
	const response = await authFetch(`${BASE}/${solutionId}`, {
		method: "DELETE",
		signal: options.signal,
	});
	if (!response.ok) {
		throw await errorFrom(response, "Failed to delete solution");
	}
}

export async function requestPromotion(
	solutionId: string,
	options: RequestOptions = {},
): Promise<BuilderProject> {
	return requestJson<BuilderProject>(
		`${BASE}/${solutionId}/promotion-request`,
		"Failed to request promotion",
		{ method: "POST", signal: options.signal },
	);
}

export async function listBuilderSessions(
	solutionId: string,
	options: RequestOptions = {},
): Promise<BuilderSession[]> {
	const result = await requestJson<{
		sessions: BuilderSession[];
		total: number;
	}>(
		`${BASE}/${solutionId}/sessions`,
		"Failed to list builder sessions",
		{ signal: options.signal },
	);
	return result.sessions;
}

export async function createBuilderSession(
	solutionId: string,
	options: RequestOptions = {},
): Promise<BuilderSession> {
	return requestJson<BuilderSession>(
		`${BASE}/${solutionId}/sessions`,
		"Failed to start a builder session",
		{ method: "POST", body: JSON.stringify({}), signal: options.signal },
	);
}

export async function listRevisions(
	solutionId: string,
	options: RequestOptions = {},
): Promise<BuilderRevision[]> {
	const result = await requestJson<{
		revisions: BuilderRevision[];
		total: number;
	}>(
		`${BASE}/${solutionId}/revisions`,
		"Failed to list revisions",
		{ signal: options.signal },
	);
	return result.revisions;
}

export async function downloadRevision(
	solutionId: string,
	revisionId: string,
	options: RequestOptions = {},
): Promise<BuilderDownload> {
	const response = await authFetch(
		`${BASE}/${solutionId}/revisions/${revisionId}/download`,
		{ signal: options.signal },
	);
	if (!response.ok) {
		throw await errorFrom(response, "Failed to download revision");
	}
	return {
		blob: await response.blob(),
		filename: filenameFrom(response, `solution-${solutionId}-source.zip`),
	};
}

export async function listRevisionFiles(
	solutionId: string,
	revisionId: string,
	options: RequestOptions = {},
): Promise<BuilderRevisionFile[]> {
	const result = await requestJson<{
		revision_id: string;
		files: BuilderRevisionFile[];
		total: number;
	}>(
		`${BASE}/${solutionId}/revisions/${revisionId}/files`,
		"Failed to list revision files",
		{ signal: options.signal },
	);
	return result.files;
}

export async function getRevisionFile(
	solutionId: string,
	revisionId: string,
	path: string,
	options: RequestOptions = {},
): Promise<BuilderRevisionFileContent> {
	const query = new URLSearchParams({ path });
	return requestJson<BuilderRevisionFileContent>(
		`${BASE}/${solutionId}/revisions/${revisionId}/file?${query.toString()}`,
		"Failed to read revision file",
		{ signal: options.signal },
	);
}

export async function getRevisionDiff(
	solutionId: string,
	revisionId: string,
	againstRevisionId?: string | null,
	options: RequestOptions = {},
): Promise<BuilderRevisionDiff> {
	const query = new URLSearchParams();
	if (againstRevisionId) {
		query.set("against_revision_id", againstRevisionId);
	}
	const suffix = query.size > 0 ? `?${query.toString()}` : "";
	return requestJson<BuilderRevisionDiff>(
		`${BASE}/${solutionId}/revisions/${revisionId}/diff${suffix}`,
		"Failed to load revision diff",
		{ signal: options.signal },
	);
}

export async function undoToRevision(
	solutionId: string,
	params: { toRevisionId: string; sessionId: string },
	options: RequestOptions = {},
): Promise<BuilderTurn> {
	return requestJson<BuilderTurn>(
		`${BASE}/${solutionId}/undo`,
		"Failed to undo",
		{
			method: "POST",
			body: JSON.stringify({
				to_revision_id: params.toRevisionId,
				session_id: params.sessionId,
			}),
			signal: options.signal,
		},
	);
}

export async function listTurns(
	solutionId: string,
	options: RequestOptions = {},
): Promise<BuilderTurn[]> {
	const result = await requestJson<{
		turns: BuilderTurn[];
		total: number;
	}>(
		`${BASE}/${solutionId}/turns`,
		"Failed to list builder turns",
		{ signal: options.signal },
	);
	return result.turns;
}

export async function runBuilderTurn(
	solutionId: string,
	params: {
		sessionId: string;
		message: string;
		resumeFromTurnId?: string;
	},
	options: RequestOptions = {},
): Promise<BuilderTurnResult> {
	return requestJson<BuilderTurnResult>(
		`${BASE}/${solutionId}/turns`,
		"Failed to run builder turn",
		{
			method: "POST",
			body: JSON.stringify({
				session_id: params.sessionId,
				message: params.message,
				...(params.resumeFromTurnId
					? { resume_from_turn_id: params.resumeFromTurnId }
					: {}),
			}),
			signal: options.signal,
		},
	);
}

export async function createBuilderAppLaunch(
	solutionId: string,
	appId: string,
	path: string,
	options: RequestOptions = {},
): Promise<BuilderLaunch> {
	const query = new URLSearchParams({ path });
	return requestJson<BuilderLaunch>(
		`${BASE}/${solutionId}/apps/${appId}/launch?${query.toString()}`,
		"Failed to launch app preview",
		{ method: "POST", signal: options.signal },
	);
}

/** The revision the preview reflects, or null when nothing has deployed yet. */
export function deployedRevision(
	revisions: BuilderRevision[],
): BuilderRevision | null {
	return revisions.find((revision) => revision.is_deployed) ?? null;
}

/** The revision the source tree reflects, or null when there are none. */
export function currentRevision(
	revisions: BuilderRevision[],
): BuilderRevision | null {
	return revisions.find((revision) => revision.is_current) ?? null;
}

/**
 * True when the current source is ahead of what the preview shows — either the
 * last build failed or has not run yet. Drives the "stale preview" badge.
 */
export function isPreviewStale(revisions: BuilderRevision[]): boolean {
	const current = currentRevision(revisions);
	const deployed = deployedRevision(revisions);
	if (!current || !deployed) return false;
	return current.id !== deployed.id;
}

/** The most recent turn, which drives the top-bar build status. */
export function latestTurn(turns: BuilderTurn[]): BuilderTurn | null {
	if (turns.length === 0) return null;
	return turns.reduce((newest, turn) =>
		turn.created_at > newest.created_at ? turn : newest,
	);
}
