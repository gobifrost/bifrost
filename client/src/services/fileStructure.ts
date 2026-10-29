import { authFetch } from "@/lib/api-client";

export type StructureScope = string | null;

function withSolution(path: string, solution?: string): string {
	return solution
		? `${path}?${new URLSearchParams({ solution }).toString()}`
		: path;
}

export interface ShareEntry {
	location: string;
	readOnly: boolean;
	hasPolicy: boolean;
}

export interface StructureEntry {
	name: string;
	kind: "folder" | "file";
	path: string;
}

async function parse<T>(res: Response): Promise<T> {
	if (res.ok) return (await res.json()) as T;
	const body = await res.json().catch(() => ({}));
	throw new Error(
		(body as { detail?: string }).detail ?? `Request failed: ${res.status}`,
	);
}

export async function listShares(
	scope: StructureScope,
	solution?: string,
): Promise<ShareEntry[]> {
	const res = await authFetch(
		withSolution("/api/files/structure", solution),
		{
			method: "POST",
			body: JSON.stringify({ scope }),
		},
	);
	const body = await parse<{
		shares?: Array<{
			location: string;
			read_only: boolean;
			has_policy: boolean;
		}>;
	}>(res);
	return (body.shares ?? []).map((s) => ({
		location: s.location,
		readOnly: s.read_only,
		hasPolicy: s.has_policy,
	}));
}

export async function listStructure(
	location: string,
	prefix: string,
	scope: StructureScope,
	solution?: string,
): Promise<StructureEntry[]> {
	const res = await authFetch(
		withSolution("/api/files/structure", solution),
		{
			method: "POST",
			body: JSON.stringify({ location, prefix, scope }),
		},
	);
	const body = await parse<{ entries?: StructureEntry[] }>(res);
	return body.entries ?? [];
}
