import { authFetch } from "@/lib/api-client";

export interface AgentSkill {
	name: string;
	description: string;
	bundle_path: string | null;
	skill_markdown: string;
	files: string[];
	companion_files: string[];
	automatic_capabilities: string[];
	source: "inline" | "upload" | "solution";
	is_managed: boolean;
}

export interface AgentSkillFile {
	path: string;
	encoding: "utf-8" | "base64";
	content: string;
}

export interface AgentSkillDownload {
	blob: Blob;
	filename: string;
}

function filenameFrom(response: Response, fallback: string): string {
	const disposition = response.headers.get("Content-Disposition") ?? "";
	const match = /filename="?([^";]+)"?/.exec(disposition);
	return match?.[1] ?? fallback;
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
	const body = await response.json().catch(() => null);
	return body && typeof body === "object" && typeof body.detail === "string"
		? body.detail
		: fallback;
}

export async function getAgentSkill(
	agentId: string,
	options: { signal?: AbortSignal } = {},
): Promise<AgentSkill> {
	const response = await authFetch(`/api/agents/${agentId}/skill`, {
		signal: options.signal,
	});
	if (!response.ok) {
		throw new Error(await errorMessage(response, "Failed to load Agent Skill"));
	}
	return (await response.json()) as AgentSkill;
}

export async function downloadAgentSkill(
	agentId: string,
	options: { signal?: AbortSignal } = {},
): Promise<AgentSkillDownload> {
	const response = await authFetch(`/api/agents/${agentId}/skill/download`, {
		signal: options.signal,
	});
	if (!response.ok) {
		throw new Error(
			await errorMessage(response, "Failed to download Agent Skill"),
		);
	}
	return {
		blob: await response.blob(),
		filename: filenameFrom(response, "agent-skill.zip"),
	};
}

export async function getAgentSkillFile(
	agentId: string,
	path: string,
	options: { signal?: AbortSignal } = {},
): Promise<AgentSkillFile> {
	const query = new URLSearchParams({ path });
	const response = await authFetch(
		`/api/agents/${agentId}/skill/file?${query.toString()}`,
		{ signal: options.signal },
	);
	if (!response.ok) {
		throw new Error(await errorMessage(response, "Failed to read Skill file"));
	}
	return (await response.json()) as AgentSkillFile;
}

export async function uploadAgentSkill(
	agentId: string,
	file: File,
): Promise<AgentSkill> {
	const body = new FormData();
	body.append("file", file);
	const response = await authFetch(`/api/agents/${agentId}/skill/bundle`, {
		method: "PUT",
		body,
	});
	if (!response.ok) {
		throw new Error(
			await errorMessage(response, "Failed to upload Agent Skill"),
		);
	}
	return (await response.json()) as AgentSkill;
}

export async function detachAgentSkill(agentId: string): Promise<void> {
	const response = await authFetch(`/api/agents/${agentId}/skill/bundle`, {
		method: "DELETE",
	});
	if (!response.ok) {
		throw new Error(
			await errorMessage(response, "Failed to remove Agent Skill bundle"),
		);
	}
}
