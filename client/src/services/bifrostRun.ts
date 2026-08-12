import { authFetch } from "@/lib/api-client";

export async function downloadBifrostRunPlugin(): Promise<{
	blob: Blob;
	filename: string;
}> {
	const response = await authFetch("/api/mcp/run/plugin");
	if (!response.ok) {
		throw new Error("Failed to download Bifrost Agent");
	}

	const disposition = response.headers.get("Content-Disposition") ?? "";
	const match = /filename="([^"]+)"/.exec(disposition);
	return {
		blob: await response.blob(),
		filename: match?.[1] ?? "bifrost-agent.zip",
	};
}
