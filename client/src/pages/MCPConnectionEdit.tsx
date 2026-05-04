/**
 * MCPConnectionEdit — per-org connection edit (mockup §5).
 *
 * Panels:
 *   - OAuth credentials (this org)            — client_id + client_secret
 *   - Optional URL overrides                  — server_url_override
 *   - Availability                            — chat / autonomous flags
 *   - Shared service connection               — connect / reconnect / disconnect
 *   - Tool catalog                            — admin enable/disable per tool
 */

import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Eye, EyeOff, Loader2, Trash2 } from "lucide-react";

import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";

import { useAuth } from "@/contexts/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { $api, apiClient } from "@/lib/api-client";
import { useOrganizations } from "@/hooks/useOrganizations";
import { toast } from "sonner";

export function MCPConnectionEdit() {
	const { serverId, connectionId } = useParams<{
		serverId: string;
		connectionId: string;
	}>();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const { user } = useAuth();

	const { data: server } = $api.useQuery(
		"get",
		"/api/mcp-servers/{server_id}",
		{ params: { path: { server_id: serverId! } } },
		{ enabled: !!serverId },
	);

	const {
		data: connection,
		isLoading,
	} = $api.useQuery(
		"get",
		"/api/mcp-connections/{connection_id}",
		{ params: { path: { connection_id: connectionId! } } },
		{ enabled: !!connectionId },
	);

	const { data: organizations = [] } = useOrganizations();
	const orgName = useMemo(() => {
		if (!connection) return null;
		return (
			organizations.find((o) => o.id === connection.organization_id)
				?.name ?? connection.organization_id
		);
	}, [organizations, connection]);

	const updateConnection = $api.useMutation(
		"patch",
		"/api/mcp-connections/{connection_id}",
	);
	const refreshTools = $api.useMutation(
		"post",
		"/api/mcp-connections/{connection_id}/refresh-tools",
	);
	const deleteConnection = $api.useMutation(
		"delete",
		"/api/mcp-connections/{connection_id}",
	);
	const [deleteOpen, setDeleteOpen] = useState(false);

	const handleDelete = async () => {
		if (!connectionId) return;
		try {
			await deleteConnection.mutateAsync({
				params: { path: { connection_id: connectionId } },
			});
			toast.success("Connection deleted");
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-servers/{server_id}"],
			});
			navigate(`/mcp-servers/${serverId}`);
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Failed to delete connection",
			);
		} finally {
			setDeleteOpen(false);
		}
	};

	// Local form state — initialised from the connection on first load.
	const [clientId, setClientId] = useState("");
	const [setNewSecret, setSetNewSecret] = useState(false);
	const [clientSecret, setClientSecret] = useState("");
	const [showSecret, setShowSecret] = useState(false);
	const [serverUrlOverride, setServerUrlOverride] = useState("");
	const [availableInChat, setAvailableInChat] = useState(false);
	const [availableToAutonomous, setAvailableToAutonomous] = useState(false);
	const [toolEnabledMap, setToolEnabledMap] = useState<
		Record<string, boolean>
	>({});

	const [connectModalOpen, setConnectModalOpen] = useState(false);

	useEffect(() => {
		// Defer state init to next tick — React Compiler flags
		// synchronous setState in effects (cascading renders).
		const timeoutId = setTimeout(() => {
			if (!connection) return;
			setClientId(connection.client_id);
			setServerUrlOverride(connection.server_url_override ?? "");
			setAvailableInChat(connection.available_in_chat);
			setAvailableToAutonomous(connection.available_to_autonomous);
			const map: Record<string, boolean> = {};
			for (const t of connection.tools ?? []) {
				map[t.id] = t.enabled;
			}
			setToolEnabledMap(map);
		}, 0);
		return () => clearTimeout(timeoutId);
	}, [connection]);

	if (isLoading) {
		return (
			<div className="space-y-4 max-w-5xl mx-auto">
				<Skeleton className="h-10 w-1/3" />
				<Skeleton className="h-32 w-full" />
				<Skeleton className="h-32 w-full" />
			</div>
		);
	}

	if (!connection || !server) {
		return (
			<Card className="max-w-3xl mx-auto">
				<CardContent className="py-12 text-center">
					<p className="text-muted-foreground">
						Connection not found.
					</p>
					<Button
						variant="outline"
						className="mt-4"
						onClick={() => navigate("/mcp-servers")}
					>
						<ArrowLeft className="h-4 w-4 mr-1" />
						Back to MCP Servers
					</Button>
				</CardContent>
			</Card>
		);
	}

	const handleSave = async () => {
		try {
			const body: Record<string, unknown> = {
				client_id: clientId,
				server_url_override: serverUrlOverride || null,
				available_in_chat: availableInChat,
				available_to_autonomous: availableToAutonomous,
			};
			if (setNewSecret && clientSecret) {
				body.client_secret = clientSecret;
			}

			await updateConnection.mutateAsync({
				params: { path: { connection_id: connection.id } },
				body,
			});

			// Persist any per-tool enabled toggles. handleSave originally
			// only saved connection-level fields; tool checkboxes were
			// collected into toolEnabledMap but never sent to the API. Issue
			// PATCHes for each tool whose enabled value diverges from the
			// last-fetched server state.
			const toolPatches: Promise<unknown>[] = [];
			for (const tool of connection.tools ?? []) {
				const desired = toolEnabledMap[tool.id];
				if (desired !== undefined && desired !== tool.enabled) {
					toolPatches.push(
						apiClient.PATCH(
							"/api/mcp-connections/{connection_id}/tools/{tool_id}",
							{
								params: {
									path: {
										connection_id: connection.id,
										tool_id: tool.id,
									},
								},
								body: { enabled: desired },
							},
						),
					);
				}
			}
			if (toolPatches.length > 0) {
				const results = await Promise.allSettled(toolPatches);
				const failed = results.filter((r) => r.status === "rejected");
				if (failed.length > 0) {
					toast.error(
						`Connection saved but ${failed.length} tool toggle(s) failed`,
					);
				} else {
					toast.success(
						`Connection saved (${toolPatches.length} tool toggle(s) applied)`,
					);
				}
			} else {
				toast.success("Connection saved");
			}

			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-connections/{connection_id}"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-servers/{server_id}"],
			});
			setSetNewSecret(false);
			setClientSecret("");
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Failed to save connection",
			);
		}
	};

	const handleRefreshTools = async () => {
		try {
			const result = await refreshTools.mutateAsync({
				params: { path: { connection_id: connection.id } },
			});
			toast.success(
				`Catalog refreshed — ${result.enabled} enabled / ${result.total} total`,
			);
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-connections/{connection_id}"],
			});
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Failed to refresh tool catalog",
			);
		}
	};

	const isConnected = !!connection.service_oauth_token_id;
	const isClientCredentials =
		server.oauth_flow_type === "client_credentials";

	return (
		<div className="space-y-6 max-w-5xl mx-auto">
			{/* Breadcrumb */}
			<div className="space-y-2">
				<button
					type="button"
					onClick={() => navigate(`/mcp-servers/${server.id}`)}
					className="text-sm text-blue-600 hover:underline inline-flex items-center"
				>
					<ArrowLeft className="h-3.5 w-3.5 mr-1" />
					{server.name}
				</button>
				<h1 className="text-3xl font-extrabold tracking-tight">
					{orgName} connection
				</h1>
			</div>

			{/* OAuth credentials */}
			<Card>
				<CardContent className="py-6 space-y-4">
					<h2 className="text-lg font-semibold">
						OAuth credentials (this org)
					</h2>
					<p className="text-xs text-muted-foreground">
						Register a confidential OAuth app in the vendor with the
						redirect URL shown on the server template, paste
						credentials here.
					</p>

					<div className="space-y-2">
						<Label htmlFor="client_id">Client ID</Label>
						<Input
							id="client_id"
							value={clientId}
							onChange={(e) => setClientId(e.target.value)}
							className="font-mono"
						/>
					</div>

					<div className="space-y-2">
						<div className="flex items-center gap-2">
							<Checkbox
								id="set_new_secret"
								checked={setNewSecret}
								onCheckedChange={(v) =>
									setSetNewSecret(v === true)
								}
							/>
							<Label
								htmlFor="set_new_secret"
								className="cursor-pointer"
							>
								Set new client secret
							</Label>
						</div>
						<p className="text-xs text-muted-foreground">
							Existing secret is preserved unless this is checked.
						</p>
					</div>

					{setNewSecret && (
						<div className="space-y-2">
							<Label htmlFor="client_secret">
								New client secret
							</Label>
							<div className="flex gap-2">
								<Input
									id="client_secret"
									type={showSecret ? "text" : "password"}
									value={clientSecret}
									onChange={(e) =>
										setClientSecret(e.target.value)
									}
									placeholder="••••••••••••••••"
								/>
								<Button
									type="button"
									variant="outline"
									size="icon"
									onClick={() => setShowSecret((s) => !s)}
								>
									{showSecret ? (
										<EyeOff className="h-4 w-4" />
									) : (
										<Eye className="h-4 w-4" />
									)}
								</Button>
							</div>
						</div>
					)}
				</CardContent>
			</Card>

			{/* Optional URL overrides */}
			<Card>
				<CardContent className="py-6 space-y-4">
					<h2 className="text-lg font-semibold">
						Optional URL overrides{" "}
						<span className="text-sm font-normal text-muted-foreground">
							(usually empty)
						</span>
					</h2>
					<div className="space-y-2">
						<Label htmlFor="server_url_override">
							Server URL override
						</Label>
						<Input
							id="server_url_override"
							value={serverUrlOverride}
							onChange={(e) =>
								setServerUrlOverride(e.target.value)
							}
							placeholder={`(uses server template: ${server.server_url})`}
							className="font-mono text-xs"
						/>
						<p className="text-xs text-muted-foreground">
							Set this only if this org points at a different
							vendor deployment than the server template (e.g.,
							regional / sovereign cloud).
						</p>
					</div>
				</CardContent>
			</Card>

			{/* Availability */}
			<Card>
				<CardContent className="py-6 space-y-4">
					<h2 className="text-lg font-semibold">Availability</h2>

					<div className="flex items-start gap-3">
						<Checkbox
							id="available_in_chat"
							checked={availableInChat}
							onCheckedChange={(v) =>
								setAvailableInChat(v === true)
							}
							className="mt-0.5"
						/>
						<div>
							<Label
								htmlFor="available_in_chat"
								className="cursor-pointer font-semibold"
							>
								Available in user chat
							</Label>
							<p className="text-xs text-muted-foreground mt-1">
								Use the shared service connection as a fallback
								when a chat user hasn't completed their own
								personal OAuth.{" "}
								<em>
									Recommended only when the service account is
									a dedicated bifrost-service@ account, not a
									real user's.
								</em>
							</p>
						</div>
					</div>

					<div className="flex items-start gap-3">
						<Checkbox
							id="available_to_autonomous"
							checked={availableToAutonomous}
							onCheckedChange={(v) =>
								setAvailableToAutonomous(v === true)
							}
							className="mt-0.5"
						/>
						<div>
							<Label
								htmlFor="available_to_autonomous"
								className="cursor-pointer font-semibold"
							>
								Available to autonomous agents
							</Label>
							<p className="text-xs text-muted-foreground mt-1">
								Schedules and webhook-triggered runs use the
								shared service connection. Without this,
								autonomous agents cannot invoke this server's
								tools.
							</p>
						</div>
					</div>

					<p className="text-xs text-muted-foreground/80 pt-2">
						Both unchecked = personal-use only. Users still need to
						OAuth individually.
					</p>
				</CardContent>
			</Card>

			{/* Shared service connection */}
			<Card>
				<CardContent className="py-6 space-y-4">
					<h2 className="text-lg font-semibold">
						Shared service connection
					</h2>

					<div className="flex items-center justify-between gap-4">
						<div>
							{isConnected ? (
								<>
									<Badge
										variant="default"
										className="bg-green-600 hover:bg-green-700"
									>
										Connected
									</Badge>
									<p className="text-xs text-muted-foreground mt-1">
										Service token linked. Refresh handled
										automatically by the OAuth refresh job.
									</p>
								</>
							) : (
								<>
									<Badge
										variant="default"
										className="bg-amber-600 hover:bg-amber-700"
									>
										Not connected
									</Badge>
									<p className="text-xs text-muted-foreground mt-1">
										No shared service token. The chat /
										autonomous fallback flags above won't
										take effect until you connect.
									</p>
								</>
							)}
						</div>
						<div className="flex gap-2">
							{isClientCredentials ? (
								<ActivateButton
									connectionId={connection.id}
									isConnected={isConnected}
								/>
							) : (
								<Button
									variant="outline"
									onClick={() => setConnectModalOpen(true)}
								>
									{isConnected ? "Reconnect" : "Connect"}
								</Button>
							)}
							{isConnected && (
								<Button
									variant="outline"
									className="text-red-600 hover:text-red-700"
									disabled={updateConnection.isPending}
									onClick={async () => {
										try {
											await updateConnection.mutateAsync({
												params: {
													path: {
														connection_id:
															connection.id,
													},
												},
												body: {
													service_oauth_token_id:
														null,
												},
											});
											toast.success(
												"Service connection cleared",
											);
											queryClient.invalidateQueries({
												queryKey: [
													"get",
													"/api/mcp-connections/{connection_id}",
												],
											});
										} catch (err) {
											toast.error(
												err instanceof Error
													? err.message
													: "Failed to disconnect",
											);
										}
									}}
								>
									Disconnect
								</Button>
							)}
						</div>
					</div>
				</CardContent>
			</Card>

			{/* Tool catalog */}
			<Card>
				<CardContent className="py-6 space-y-4">
					<div className="flex items-center justify-between">
						<h2 className="text-lg font-semibold">
							Tool catalog{" "}
							<Badge
								variant="default"
								className="ml-2 bg-blue-600 hover:bg-blue-700"
							>
								{(connection.tools ?? []).length} tools ·{" "}
								{
									(connection.tools ?? []).filter(
										(t) => t.enabled,
									).length
								}{" "}
								enabled
							</Badge>
						</h2>
						<Button
							variant="outline"
							size="sm"
							disabled={refreshTools.isPending || !isConnected}
							onClick={handleRefreshTools}
							title={
								isConnected
									? "Re-fetch tools/list from the vendor"
									: "Connect first to refresh the catalog"
							}
						>
							{refreshTools.isPending ? (
								<Loader2 className="h-4 w-4 mr-1 animate-spin" />
							) : null}
							Refresh catalog
						</Button>
					</div>

					{(connection.tools ?? []).length === 0 ? (
						<p className="text-sm text-muted-foreground">
							No tools cached. Refresh the catalog after the
							service connection is healthy to populate.
						</p>
					) : (
						<>
							{/*
							 * Auth-context summary at the connection level.
							 * The MCP protocol's tools/list response doesn't
							 * carry per-tool auth-context metadata, so we
							 * describe how this connection will resolve
							 * tokens once, instead of slapping a misleading
							 * badge on every row.
							 */}
							<div className="text-xs text-muted-foreground border-l-2 border-blue-500 bg-blue-500/5 px-3 py-2 rounded-sm">
								{isClientCredentials ? (
									<>
										<strong>Server-to-server auth:</strong>{" "}
										every tool call uses the shared service
										token (no per-user mode in
										client_credentials flow).
									</>
								) : availableInChat &&
									availableToAutonomous ? (
									<>
										Tools resolve to the calling user's
										personal token if connected; fall back
										to the shared service token in chat
										and autonomous runs.
									</>
								) : availableInChat ? (
									<>
										Tools resolve to the calling user's
										personal token if connected; fall back
										to the shared service token in chat
										only.{" "}
										<em>
											Autonomous agent runs cannot use
											these tools until "Available to
											autonomous agents" is enabled.
										</em>
									</>
								) : availableToAutonomous ? (
									<>
										Tools resolve to the calling user's
										personal token if connected. Autonomous
										runs use the shared service token.{" "}
										<em>
											Chat users without a personal
											connection get a connect prompt.
										</em>
									</>
								) : (
									<>
										<strong>Per-user only:</strong> users
										must individually OAuth their account;
										no shared service fallback. Autonomous
										agent runs cannot use these tools.
									</>
								)}
							</div>
							<DataTable>
							<DataTableHeader>
								<DataTableRow>
									<DataTableHead className="w-10">
										Enabled
									</DataTableHead>
									<DataTableHead>Tool</DataTableHead>
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{(connection.tools ?? []).map((tool) => {
									const enabled =
										toolEnabledMap[tool.id] ?? tool.enabled;
									return (
										<DataTableRow key={tool.id}>
											<DataTableCell>
												<Checkbox
													checked={enabled}
													onCheckedChange={(v) =>
														setToolEnabledMap(
															(prev) => ({
																...prev,
																[tool.id]:
																	v === true,
															}),
														)
													}
												/>
											</DataTableCell>
											<DataTableCell>
												<code className="text-sm">
													{tool.tool_name}
												</code>
												{tool.disabled_reason && (
													<div className="text-xs text-muted-foreground mt-0.5">
														{tool.disabled_reason}
													</div>
												)}
											</DataTableCell>
										</DataTableRow>
									);
								})}
							</DataTableBody>
						</DataTable>
						</>
					)}
					<p className="text-xs text-muted-foreground">
						Catalog is per-connection: the vendor's tools/list
						response after this org's service-account OAuth. Other
						orgs may see different tools.
					</p>
				</CardContent>
			</Card>

			{/* Save / Cancel / Delete */}
			<div className="flex items-center gap-2 pt-2">
				<Button
					onClick={handleSave}
					disabled={updateConnection.isPending}
				>
					{updateConnection.isPending ? (
						<>
							<Loader2 className="h-4 w-4 mr-2 animate-spin" />
							Saving...
						</>
					) : (
						"Save"
					)}
				</Button>
				<Button
					variant="outline"
					onClick={() => navigate(`/mcp-servers/${server.id}`)}
				>
					Cancel
				</Button>
				<div className="ml-auto">
					<Button
						variant="outline"
						className="text-rose-600 hover:bg-rose-50 hover:text-rose-700 border-rose-200"
						onClick={() => setDeleteOpen(true)}
					>
						<Trash2 className="h-4 w-4 mr-1" />
						Delete connection
					</Button>
				</div>
			</div>

			<AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete this connection?</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently remove the connection,{" "}
							its cached tool catalog, and all per-user
							credentials linked to it. Agents using these
							tools will lose access immediately. This cannot
							be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={deleteConnection.isPending}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDelete}
							disabled={deleteConnection.isPending}
							className="bg-rose-600 hover:bg-rose-700 text-white"
						>
							{deleteConnection.isPending
								? "Deleting..."
								: "Delete connection"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{!isClientCredentials && (
				<ConnectServicePopup
					open={connectModalOpen}
					onOpenChange={setConnectModalOpen}
					connectionId={connection.id}
					serverName={server.name}
					userEmail={user?.email ?? "your account"}
				/>
			)}
		</div>
	);
}

/**
 * Activate button for ``client_credentials`` connections.
 *
 * Posts to ``/api/mcp-connections/{id}/connect`` synchronously — no popup.
 * The backend exchanges the connection's client_id+secret for a token and
 * persists it as ``service_oauth_token_id``. We refetch the connection on
 * success so ``isConnected`` flips to ``true``.
 */
function ActivateButton({
	connectionId,
	isConnected,
}: {
	connectionId: string;
	isConnected: boolean;
}) {
	const queryClient = useQueryClient();
	const [activating, setActivating] = useState(false);

	const handleActivate = async () => {
		setActivating(true);
		try {
			const { data, error } = await apiClient.POST(
				"/api/mcp-connections/{connection_id}/connect",
				{ params: { path: { connection_id: connectionId } } },
			);
			if (error) {
				const detail =
					(error as { detail?: string })?.detail ??
					"Activation failed";
				toast.error(detail);
				return;
			}
			if (data && "flow" in data && data.flow === "client_credentials") {
				toast.success("Connection activated");
			} else {
				// Backend returned an authorization_code response — the
				// server is misconfigured (provider flow_type was changed
				// after the form was rendered). Surface gracefully.
				toast.error(
					"Server returned an authorization flow — refresh the page",
				);
				return;
			}
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-connections/{connection_id}"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-servers/{server_id}"],
			});
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Activation failed",
			);
		} finally {
			setActivating(false);
		}
	};

	return (
		<Button
			variant="outline"
			disabled={activating}
			onClick={handleActivate}
		>
			{activating ? (
				<>
					<Loader2 className="h-4 w-4 mr-2 animate-spin" />
					Activating...
				</>
			) : isConnected ? (
				"Reactivate connection"
			) : (
				"Activate connection"
			)}
		</Button>
	);
}

/**
 * Connect popup (mockup §6).
 *
 * Displays Jack's mandated wording before opening the OAuth window so the
 * admin reads the consequence in user terms before consenting.
 */
function ConnectServicePopup({
	open,
	onOpenChange,
	connectionId,
	serverName,
	userEmail,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	connectionId: string;
	serverName: string;
	userEmail: string;
}) {
	const [starting, setStarting] = useState(false);

	const handleContinue = async () => {
		setStarting(true);
		try {
			const { data, error } = await apiClient.POST(
				"/api/mcp-connections/{connection_id}/connect",
				{ params: { path: { connection_id: connectionId } } },
			);

			if (
				error ||
				!data ||
				!("flow" in data) ||
				data.flow !== "authorization_code"
			) {
				toast.error("Failed to start OAuth flow");
				return;
			}

			window.open(
				data.authorization_url,
				"_blank",
				"width=600,height=700",
			);
			onOpenChange(false);
			toast.success(
				"Authorization started — complete it in the popup window",
			);
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Failed to start OAuth flow",
			);
		} finally {
			setStarting(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Connect {serverName}</DialogTitle>
				</DialogHeader>

				<div className="space-y-3">
					<p className="text-sm">
						You're about to authorize Bifrost to access{" "}
						{serverName} on your behalf.
					</p>

					<div className="rounded-md border-l-4 border-amber-500 bg-amber-50 dark:bg-amber-950/20 p-3 text-sm">
						<p>
							<strong>This connection will be shared.</strong>{" "}
							users will read and modify resources visible to{" "}
							<strong>{userEmail}</strong>'s account — recommended
							only for dedicated service accounts, not personal
							accounts.
						</p>
					</div>

					<p className="text-xs text-muted-foreground">
						Continuing will redirect you to the vendor's sign-in.
					</p>
				</div>

				<DialogFooter>
					<Button
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={starting}
					>
						Cancel
					</Button>
					<Button onClick={handleContinue} disabled={starting}>
						{starting ? (
							<>
								<Loader2 className="h-4 w-4 mr-2 animate-spin" />
								Starting...
							</>
						) : (
							"Continue to sign-in"
						)}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
