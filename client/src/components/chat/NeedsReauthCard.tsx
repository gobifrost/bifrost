/**
 * NeedsReauthCard — inline reconnect prompt for chat (mockup §9).
 *
 * Rendered when a tool result has ``error_type === "needs_reauth"`` (Phase
 * 3 contract on ``ToolResult``). Carries a ``metadata.connection_id``
 * which we resolve into a friendly service name via React Query, plus a
 * ``metadata.reauth_url`` we use to build the OAuth popup target.
 *
 * Pattern matches AskUserQuestionCard — bordered card with a header,
 * descriptive copy, and a single action button. The OAuth popup flow
 * matches MCPConnectionEdit.tsx and UserMCPConnections.tsx: open the
 * `/api/me/mcp-connections/{id}/connect` URL in a sized popup, listen
 * for the ``mcp_oauth_success`` postMessage, and toast/refresh.
 */

import { useEffect, useState } from "react";
import { Plug } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { $api, apiClient } from "@/lib/api-client";

export interface NeedsReauthMetadata {
	reauth_url?: string;
	connection_id?: string;
	tool_name?: string;
}

interface NeedsReauthCardProps {
	metadata: NeedsReauthMetadata;
	/**
	 * Called after the user successfully completes the OAuth popup flow.
	 * The chat surface is responsible for kicking off a retry of the
	 * tool/turn — we don't have a generic "retry last tool" hook in this
	 * codebase yet, so the consumer wires the right action.
	 */
	onConnected?: () => void;
}

/** Detect a needs_reauth payload on an arbitrary tool_result blob. */
export function extractNeedsReauth(
	toolResult: unknown,
): NeedsReauthMetadata | null {
	if (!toolResult || typeof toolResult !== "object") return null;
	const r = toolResult as Record<string, unknown>;
	if (r.error_type !== "needs_reauth") return null;
	const meta = (r.metadata ?? {}) as Record<string, unknown>;
	return {
		reauth_url: typeof meta.reauth_url === "string" ? meta.reauth_url : undefined,
		connection_id:
			typeof meta.connection_id === "string" ? meta.connection_id : undefined,
		tool_name: typeof meta.tool_name === "string" ? meta.tool_name : undefined,
	};
}

export function NeedsReauthCard({
	metadata,
	onConnected,
}: NeedsReauthCardProps) {
	const queryClient = useQueryClient();
	const [starting, setStarting] = useState(false);

	const connectionId = metadata.connection_id ?? null;

	// Look up the connection (and via it the server) to render a friendly
	// service name. Falls back to "this service" if any of the lookups
	// fail or the connection ID is missing.
	const { data: connection } = $api.useQuery(
		"get",
		"/api/mcp-connections/{connection_id}",
		{ params: { path: { connection_id: connectionId ?? "" } } },
		{ enabled: !!connectionId },
	);
	const { data: server } = $api.useQuery(
		"get",
		"/api/mcp-servers/{server_id}",
		{ params: { path: { server_id: connection?.server_id ?? "" } } },
		{ enabled: !!connection?.server_id },
	);

	const serviceName = server?.name ?? "this service";

	useEffect(() => {
		// Listen for the popup callback's postMessage. When we see
		// success for our connection, fire onConnected and drop a toast.
		function handleMessage(ev: MessageEvent) {
			if (ev.origin !== window.location.origin) return;
			const data = ev.data as
				| { type?: string; connection_id?: string; error?: string }
				| null;
			if (!data || typeof data !== "object") return;
			if (
				data.type === "mcp_oauth_success" &&
				connectionId &&
				data.connection_id === connectionId
			) {
				toast.success(`Connected — please retry your message`);
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/mcp-connections"],
				});
				onConnected?.();
			} else if (
				data.type === "mcp_oauth_error" &&
				connectionId &&
				data.connection_id === connectionId
			) {
				toast.error(
					`${serviceName} connection failed: ${data.error ?? "unknown"}`,
				);
			}
		}
		window.addEventListener("message", handleMessage);
		return () => window.removeEventListener("message", handleMessage);
	}, [queryClient, serviceName, connectionId, onConnected]);

	async function handleConnect() {
		if (!connectionId) {
			toast.error("Missing connection — cannot start reauth");
			return;
		}
		setStarting(true);
		try {
			const { data, error } = await apiClient.GET(
				"/api/me/mcp-connections/{connection_id}/connect",
				{ params: { path: { connection_id: connectionId } } },
			);
			if (error || !data?.authorization_url) {
				toast.error(`Failed to start ${serviceName} OAuth flow`);
				return;
			}
			const popup = window.open(
				data.authorization_url,
				"mcp_user_oauth",
				"width=600,height=720",
			);
			if (!popup) {
				toast.error(
					"Popup blocked — please allow popups for this site and try again",
				);
			}
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: `Failed to start ${serviceName} OAuth flow`,
			);
		} finally {
			setStarting(false);
		}
	}

	return (
		<div
			className="my-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:bg-amber-950/30"
			data-testid="needs-reauth-card"
		>
			<div className="flex items-center gap-2 font-semibold text-amber-900 dark:text-amber-200">
				<Plug className="h-4 w-4" />
				Connect {serviceName} to continue
			</div>
			<p className="mt-1 text-amber-800/90 dark:text-amber-200/90">
				This tool needs your personal access to return results filtered by
				the documents you can see.
			</p>
			<div className="mt-2 flex items-center gap-2">
				<Button size="sm" onClick={handleConnect} disabled={starting}>
					{starting ? "Starting…" : `Connect ${serviceName}`}
				</Button>
				<span className="text-xs text-muted-foreground">
					One-time consent · refreshes silently for 90 days
				</span>
			</div>
		</div>
	);
}
