/**
 * Private Solution Builder service.
 *
 * Wraps the `/api/builder/*` control plane: private Solutions, builder chat
 * sessions, source revisions, undo, and agent turns.
 *
 * The DTO interfaces below are declared locally because the builder routes are
 * still landing; they move to `components["schemas"][...]` once the OpenAPI
 * spec regenerates.
 */

import { authFetch } from "@/lib/api-client";

export type SolutionVisibility = "private" | "shared";

export interface BuilderSolution {
	id: string;
	slug: string;
	name: string;
	visibility: SolutionVisibility;
	owner_user_id: string | null;
	organization_id: string | null;
	app_origin?: string | null;
	status: string;
	promotion_status: string | null;
	created_at: string;
	updated_at: string;
}

export interface BuilderProject {
	solution_id: string;
	current_revision_id: string | null;
	deployed_revision_id: string | null;
	promotion_status: string;
	created_at: string;
	updated_at: string;
}

export interface BuilderSession {
	id: string;
	solution_id: string;
	conversation_id: string;
	user_id: string;
	created_at: string;
}

export interface BuilderRevision {
	id: string;
	parent_revision_id: string | null;
	restored_from_revision_id: string | null;
	source_sha256: string;
	size_bytes: number;
	summary: string | null;
	created_at: string;
	created_by: string | null;
	is_current: boolean;
	is_deployed: boolean;
}

export interface BuilderRevisionFile {
	path: string;
	size_bytes: number;
	is_text: boolean;
}

export interface BuilderRevisionFileContent {
	revision_id: string;
	path: string;
	size_bytes: number;
	encoding: "utf-8" | "binary";
	content: string | null;
	truncated: boolean;
}

export interface BuilderRevisionDiffFile {
	path: string;
	status: "added" | "modified" | "deleted";
	additions: number;
	deletions: number;
	is_binary: boolean;
	diff: string | null;
	truncated: boolean;
}

export interface BuilderRevisionDiff {
	revision_id: string;
	against_revision_id: string | null;
	files: BuilderRevisionDiffFile[];
	total: number;
	additions: number;
	deletions: number;
}

export type BuilderTurnStatus = "queued" | "running" | "succeeded" | "failed";

export interface BuilderTurn {
	id: string;
	session_id: string;
	status: BuilderTurnStatus;
	error: string | null;
	base_revision_id: string | null;
	output_revision_id: string | null;
	build_job_id: string | null;
	deploy_job_id: string | null;
	created_at: string;
	started_at: string | null;
	completed_at: string | null;
}

export interface BuilderTurnResult {
	turn: BuilderTurn;
	final_text: string;
	tool_call_count: number;
	revision_created: boolean;
}

export interface CreateBuilderSolutionRequest {
	slug: string;
	name: string;
}

export interface BuilderLaunch {
	launch_url: string;
}

export interface BuilderDownload {
	blob: Blob;
	filename: string;
}

export interface BuilderSolutionsList {
	solutions: BuilderSolution[];
	total: number;
	ai_configured: boolean;
	is_platform_admin: boolean;
}

interface RequestOptions {
	signal?: AbortSignal;
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
	const detail =
		body && typeof body === "object" && typeof body.detail === "string"
			? body.detail
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
	options: RequestOptions = {},
): Promise<BuilderSolutionsList> {
	return requestJson<BuilderSolutionsList>(
		BASE,
		"Failed to list builder solutions",
		{ signal: options.signal },
	);
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
	params: { sessionId: string; message: string },
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
