/**
 * UserMCPConnections — "My Connections" tab in user settings (mockup §7).
 *
 * Lists all MCP connections the org admin has set up that the user can
 * opt into for personalized access. Connecting opens an OAuth popup at
 * GET /api/me/mcp-connections/{id}/connect; the popup callback posts
 * back via window.opener.postMessage({type: "mcp_oauth_success", ...})
 * and we invalidate the connection list query.
 *
 * Backend endpoints:
 *   - GET /api/me/mcp-connections lists the caller's per-user credentials
 *     (consent_granted_at, consent_expires_at, granted_scopes) so the row
 *     can show Connected / Not connected and expiration timing.
 *   - DELETE /api/me/mcp-connections/{id} forgets a user_mcp_credentials
 *     row (idempotent: returns 204 whether or not it existed).
 */

import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Plug, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import { $api, apiClient } from "@/lib/api-client";

interface ConnectionRow {
	connection_id: string;
	server_id: string;
	server_name: string;
	available_in_chat: boolean;
	available_to_autonomous: boolean;
	has_service_token: boolean;
	connected: boolean;
	consent_granted_at: string | null;
	consent_expires_at: string | null;
}

function formatRelativeFromNow(iso: string | null, kind: "since" | "in"): string | null {
	if (!iso) return null;
	const t = new Date(iso).getTime();
	const now = Date.now();
	const deltaMs = kind === "since" ? now - t : t - now;
	if (deltaMs < 0) return kind === "in" ? "expired" : null;
	const days = Math.round(deltaMs / 86400000);
	if (days >= 2) return kind === "since" ? `since ${days}d ago` : `in ${days}d`;
	const hours = Math.round(deltaMs / 3600000);
	if (hours >= 2) return kind === "since" ? `since ${hours}h ago` : `in ${hours}h`;
	return kind === "since" ? "moments ago" : "soon";
}

export function UserMCPConnections() {
	const queryClient = useQueryClient();

	// All connections visible to the user (org-scoped on the server side).
	const {
		data: connections = [],
		isLoading: connsLoading,
		refetch,
	} = $api.useQuery("get", "/api/mcp-connections", {
		params: { query: {} },
	});

	// Server templates (so we can label rows with the service name).
	const { data: servers = [], isLoading: serversLoading } = $api.useQuery(
		"get",
		"/api/mcp-servers",
		{ params: { query: { active_only: false } } },
	);

	// The caller's per-user credentials (one row per connection they've connected).
	const { data: credentials = [], isLoading: credsLoading } = $api.useQuery(
		"get",
		"/api/me/mcp-connections",
	);

	const [pendingDisconnect, setPendingDisconnect] = useState<string | null>(null);

	const isLoading = connsLoading || serversLoading || credsLoading;

	const rows: ConnectionRow[] = useMemo(() => {
		const serverById = new Map<string, string>();
		for (const s of servers) serverById.set(s.id, s.name);
		const credByConn = new Map<string, (typeof credentials)[number]>();
		for (const c of credentials) credByConn.set(c.connection_id, c);
		return connections.map((c) => {
			const cred = credByConn.get(c.id);
			return {
				connection_id: c.id,
				server_id: c.server_id,
				server_name: serverById.get(c.server_id) ?? "Unknown service",
				available_in_chat: c.available_in_chat,
				available_to_autonomous: c.available_to_autonomous,
				has_service_token: c.service_oauth_token_id != null,
				connected: cred != null,
				consent_granted_at: cred?.consent_granted_at ?? null,
				consent_expires_at: cred?.consent_expires_at ?? null,
			};
		});
	}, [connections, servers, credentials]);

	// Listen for the popup's success message and invalidate the list. The
	// callback page (api/src/routers/mcp_oauth_callback.py) posts a
	// {type: 'mcp_oauth_success', connection_id} message back to the opener.
	useEffect(() => {
		function handleMessage(ev: MessageEvent) {
			if (ev.origin !== window.location.origin) return;
			const data = ev.data as { type?: string; connection_id?: string } | null;
			if (!data || typeof data !== "object") return;
			if (data.type === "mcp_oauth_success") {
				toast.success("Connected — your personal access is now linked");
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/mcp-connections"],
				});
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/me/mcp-connections"],
				});
			} else if (data.type === "mcp_oauth_error") {
				toast.error(
					`Connection failed: ${(data as { error?: string }).error ?? "unknown"}`,
				);
			}
		}
		window.addEventListener("message", handleMessage);
		return () => window.removeEventListener("message", handleMessage);
	}, [queryClient]);

	async function handleConnect(connectionId: string, serverName: string) {
		try {
			const { data, error } = await apiClient.GET(
				"/api/me/mcp-connections/{connection_id}/connect",
				{ params: { path: { connection_id: connectionId } } },
			);
			if (error || !data?.authorization_url) {
				toast.error(`Failed to start ${serverName} OAuth flow`);
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
				return;
			}
			toast.message(`Continue ${serverName} sign-in in the popup window`);
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Failed to start OAuth flow",
			);
		}
	}

	async function handleDisconnect(connectionId: string, serverName: string) {
		setPendingDisconnect(connectionId);
		try {
			const { error } = await apiClient.DELETE(
				"/api/me/mcp-connections/{connection_id}",
				{ params: { path: { connection_id: connectionId } } },
			);
			if (error) {
				toast.error(`Failed to disconnect ${serverName}`);
				return;
			}
			toast.success(`Disconnected from ${serverName}`);
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/me/mcp-connections"],
			});
		} finally {
			setPendingDisconnect(null);
		}
	}

	function fallbackLabel(row: ConnectionRow) {
		if (row.available_in_chat && row.has_service_token) {
			return (
				<>
					<span className="text-foreground">Shared service account</span>
					<div className="text-xs text-muted-foreground">
						(if you disconnect)
					</div>
				</>
			);
		}
		return (
			<>
				<Badge variant="default" className="bg-rose-600 hover:bg-rose-700">
					No fallback
				</Badge>
				<div className="text-xs text-muted-foreground">
					Tools disabled until you connect
				</div>
			</>
		);
	}

	return (
			<Card>
				<CardContent className="py-6 space-y-4">
					<div className="flex items-center justify-between">
						<div>
							<h2 className="text-lg font-semibold">My Connections</h2>
							<p className="mt-1 text-sm text-muted-foreground max-w-2xl">
								Connect your account to external tools so agents can act
								with your identity (and your permissions). Without a
								personal connection, the agent uses the shared org service
								account if your admin has enabled it.
							</p>
						</div>
						<Button
							variant="outline"
							size="sm"
							onClick={() => refetch()}
							title="Refresh"
						>
							<RefreshCw className="h-4 w-4 mr-1" />
							Refresh
						</Button>
					</div>

					{isLoading ? (
						<div className="space-y-2">
							{[...Array(3)].map((_, i) => (
								<Skeleton key={i} className="h-12 w-full" />
							))}
						</div>
					) : rows.length === 0 ? (
						<div className="border rounded-md py-12 text-center">
							<Plug className="h-10 w-10 mx-auto text-muted-foreground" />
							<h3 className="mt-3 font-semibold">No connections available</h3>
							<p className="mt-1 text-sm text-muted-foreground max-w-md mx-auto">
								No MCP services have been set up for your organization yet.
								Ask an admin to add a connection.
							</p>
						</div>
					) : (
						<DataTable>
							<DataTableHeader>
								<DataTableRow>
									<DataTableHead>Service</DataTableHead>
									<DataTableHead>Your status</DataTableHead>
									<DataTableHead>Org default</DataTableHead>
									<DataTableHead className="text-right">
										Actions
									</DataTableHead>
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{rows.map((row) => (
									<DataTableRow key={row.connection_id}>
										<DataTableCell className="font-medium">
											{row.server_name}
										</DataTableCell>
										<DataTableCell>
											{row.connected ? (
												<>
													<Badge variant="default" className="bg-emerald-600 hover:bg-emerald-700">
														Connected
													</Badge>
													<div className="text-xs text-muted-foreground mt-1">
														{[
															formatRelativeFromNow(row.consent_granted_at, "since"),
															row.consent_expires_at
																? `expires ${formatRelativeFromNow(row.consent_expires_at, "in")}`
																: null,
														]
															.filter(Boolean)
															.join(" · ")}
													</div>
												</>
											) : (
												<>
													<Badge variant="secondary">Not connected</Badge>
													<div className="text-xs text-muted-foreground mt-1">
														Click Connect to link your account
													</div>
												</>
											)}
										</DataTableCell>
										<DataTableCell>{fallbackLabel(row)}</DataTableCell>
										<DataTableCell className="text-right">
											<div className="inline-flex items-center gap-2">
												<Button
													size="sm"
													onClick={() =>
														handleConnect(
															row.connection_id,
															row.server_name,
														)
													}
												>
													{row.connected ? "Reconnect" : "Connect"}
												</Button>
												<Button
													variant="outline"
													size="sm"
													disabled={!row.connected || pendingDisconnect === row.connection_id}
													className="text-rose-600 disabled:text-muted-foreground"
													onClick={() =>
														handleDisconnect(row.connection_id, row.server_name)
													}
												>
													{pendingDisconnect === row.connection_id ? (
														<Loader2 className="h-3 w-3 animate-spin" />
													) : (
														"Disconnect"
													)}
												</Button>
											</div>
										</DataTableCell>
									</DataTableRow>
								))}
							</DataTableBody>
						</DataTable>
					)}

				</CardContent>
			</Card>
	);
}

// Re-export under a default for lazy/dynamic imports if ever needed.
export default UserMCPConnections;

// Trivial loader to keep this file minimal in JSX usage.
export function UserMCPConnectionsLoader() {
	return (
		<div className="flex items-center justify-center py-12">
			<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
		</div>
	);
}
