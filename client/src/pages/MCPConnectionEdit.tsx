import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ConnectionToolCatalog } from "./mcp/components/ConnectionToolCatalog";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
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

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
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
import { Switch } from "@/components/ui/switch";

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
import { useOrganizationName } from "@/hooks/useOrganizationName";
import { toast } from "sonner";

export function MCPConnectionEdit() {
	const { serverId, connectionId } = useParams<{
		serverId: string;
		connectionId: string;
	}>();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const { user } = useAuth();

	const {
		data: server,
		isLoading: serverLoading,
		error: serverError,
		refetch: refetchServer,
		isFetching: serverFetching,
	} = $api.useQuery(
		"get",
		"/api/mcp-servers/{server_id}",
		{ params: { path: { server_id: serverId! } } },
		{ enabled: !!serverId },
	);

	const {
		data: connection,
		isLoading,
		error: connectionError,
		refetch: refetchConnection,
		isFetching: connectionFetching,
	} = $api.useQuery(
		"get",
		"/api/mcp-connections/{connection_id}",
		{ params: { path: { connection_id: connectionId! } } },
		{ enabled: !!connectionId },
	);

	const { data: organizations = [] } = useOrganizations();
	const getOrgName = useOrganizationName(organizations);
	const orgName = useMemo(() => {
		if (!connection) return null;
		return getOrgName(connection.organization_id);
	}, [connection, getOrgName]);

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
		if (!connectionId || deleteConnection.isPending) return;
		try {
			await deleteConnection.mutateAsync({
				params: { path: { connection_id: connectionId } },
			});
			setDeleteOpen(false);
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

	const initializedConnection = useRef<string | null>(null);
	const mutationBusy = useRef(false);
	const [isSaving, setIsSaving] = useState(false);
	const [serviceAction, setServiceAction] = useState<
		"disconnect" | "catalog" | null
	>(null);
	const [disconnectError, setDisconnectError] = useState<string | null>(null);
	const [catalogError, setCatalogError] = useState<string | null>(null);
	const pagePending = isSaving || serviceAction !== null;

	const [saveError, setSaveError] = useState<string | null>(null);
	const saveErrorRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (saveError) saveErrorRef.current?.focus();
	}, [saveError]);

	const [connectModalOpen, setConnectModalOpen] = useState(false);

	useEffect(() => {
		// Defer state init to next tick — React Compiler flags
		// synchronous setState in effects (cascading renders).
		const timeoutId = setTimeout(() => {
			if (!connection || initializedConnection.current === connection.id)
				return;
			initializedConnection.current = connection.id;
			setSetNewSecret(false);
			setClientSecret("");
			setShowSecret(false);
			setSaveError(null);
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

	if ((isLoading && !connection) || (serverLoading && !server)) {
		return (
			<div
				role="status"
				aria-label="Loading connection"
				className="space-y-4 max-w-5xl mx-auto"
			>
				<Skeleton className="h-10 w-1/3" />
				<Skeleton className="h-32 w-full" />
				<Skeleton className="h-32 w-full" />
			</div>
		);
	}

	if ((!connection && connectionError) || (!server && serverError)) {
		return (
			<Alert variant="destructive" className="mx-auto max-w-3xl">
				<AlertTitle>Connection unavailable</AlertTitle>
				<AlertDescription>
					Connection details could not be loaded. Retry to check
					access and availability.
				</AlertDescription>
				<Button
					type="button"
					variant="outline"
					className="min-h-11 mt-3 w-fit"
					disabled={serverFetching || connectionFetching}
					onClick={() => {
						void refetchServer();
						void refetchConnection();
					}}
				>
					{serverFetching || connectionFetching
						? "Retrying…"
						: "Retry connection"}
				</Button>
			</Alert>
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
		if (mutationBusy.current) return;
		mutationBusy.current = true;
		setIsSaving(true);
		setSaveError(null);
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
						apiClient
							.PATCH(
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
							)
							.then((result) => {
								if (result.error)
									throw new Error("Tool update failed");
								return result.data;
							}),
					);
				}
			}
			if (toolPatches.length > 0) {
				const results = await Promise.allSettled(toolPatches);
				const failed = results.filter((r) => r.status === "rejected");
				if (failed.length > 0) {
					setSaveError(
						`Connection saved, but ${failed.length} tool ${failed.length === 1 ? "change" : "changes"} could not be saved. Your selections are preserved. Retry to apply them.`,
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
			setSaveError(
				err instanceof Error
					? err.message
					: "Failed to save connection. Your changes are preserved.",
			);
		} finally {
			mutationBusy.current = false;
			setIsSaving(false);
		}
	};

	const handleRefreshTools = async () => {
		if (mutationBusy.current) return;
		mutationBusy.current = true;
		setServiceAction("catalog");
		setCatalogError(null);
		try {
			const result = await refreshTools.mutateAsync({
				params: { path: { connection_id: connection.id } },
			});
			toast.success(
				`Catalog refreshed — ${result.enabled} enabled / ${result.total} total`,
			);
			await queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-connections/{connection_id}"],
			});
		} catch (err) {
			setCatalogError(
				err instanceof Error
					? err.message
					: "Failed to refresh tool catalog",
			);
		} finally {
			mutationBusy.current = false;
			setServiceAction(null);
		}
	};

	const handleDisconnect = async () => {
		if (mutationBusy.current) return;
		mutationBusy.current = true;
		setServiceAction("disconnect");
		setDisconnectError(null);
		try {
			await updateConnection.mutateAsync({
				params: { path: { connection_id: connection.id } },
				body: { service_oauth_token_id: null },
			});
			toast.success("Service connection cleared");
			await queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-connections/{connection_id}"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-servers/{server_id}"],
			});
		} catch {
			setDisconnectError("Could not disconnect the service. Try again.");
		} finally {
			mutationBusy.current = false;
			setServiceAction(null);
		}
	};

	const isConnected = !!connection.service_oauth_token_id;
	const isClientCredentials = server.oauth_flow_type === "client_credentials";

	return (
		<PageWorkspace className="max-w-5xl mx-auto">
			{(connectionError || serverError) && (
				<Alert variant="destructive">
					<AlertDescription>
						Connection details could not refresh. Your changes are
						preserved.
					</AlertDescription>
					<Button
						type="button"
						variant="outline"
						className="mt-3 min-h-11"
						disabled={
							connectionFetching || serverFetching || pagePending
						}
						onClick={() => {
							void refetchConnection();
							void refetchServer();
						}}
					>
						Retry connection
					</Button>
				</Alert>
			)}
			{/* Breadcrumb */}
			<div className="space-y-2">
				<Link
					to={`/mcp-servers/${server.id}`}
					className="min-h-11 text-sm text-primary hover:underline inline-flex items-center"
				>
					<ArrowLeft className="h-3.5 w-3.5 mr-1" />
					{server.name}
				</Link>
				<h1 className="font-display text-2xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-3xl">
					{orgName} connection
				</h1>
			</div>

			<PageScrollArea>
				<fieldset disabled={pagePending} className="min-w-0 space-y-6">
					{/* OAuth credentials */}
					<Card>
						<CardContent className="py-6 space-y-4">
							<h2 className="text-lg font-semibold">
								OAuth credentials (this org)
							</h2>
							<p className="text-xs text-muted-foreground">
								Register a confidential OAuth app in the vendor
								with the redirect URL shown on the server
								template, paste credentials here.
							</p>

							<div className="space-y-2">
								<Label htmlFor="client_id">Client ID</Label>
								<Input
									id="client_id"
									value={clientId}
									onChange={(e) =>
										setClientId(e.target.value)
									}
									className="min-h-11 font-mono"
								/>
							</div>

							<div className="space-y-2">
								<div className="flex items-center gap-2">
									<Switch
										id="set_new_secret"
										checked={setNewSecret}
										onCheckedChange={(v) =>
											setSetNewSecret(v === true)
										}
									/>
									<Label
										htmlFor="set_new_secret"
										className="flex min-h-11 cursor-pointer items-center"
									>
										Set new client secret
									</Label>
								</div>
								<p className="text-xs text-muted-foreground">
									Existing secret is preserved unless this is
									checked.
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
											className="min-h-11 min-w-0 flex-1"
											autoComplete="new-password"
											type={
												showSecret ? "text" : "password"
											}
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
											className="min-h-11 min-w-11 shrink-0"
											aria-label={
												showSecret
													? "Hide client secret"
													: "Show client secret"
											}
											aria-controls="client_secret"
											onClick={() =>
												setShowSecret((s) => !s)
											}
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
									className="min-h-11 font-mono text-xs"
								/>
								<p className="text-xs text-muted-foreground">
									Set this only if this org points at a
									different vendor deployment than the server
									template (e.g., regional / sovereign cloud).
								</p>
							</div>
						</CardContent>
					</Card>

					{/* Availability */}
					<Card>
						<CardContent className="py-6 space-y-4">
							<h2 className="text-lg font-semibold">
								Availability
							</h2>

							<div className="flex items-start gap-3">
								<Switch
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
										className="flex min-h-11 cursor-pointer items-center font-semibold"
									>
										Available in user chat
									</Label>
									<p className="text-xs text-muted-foreground mt-1">
										Use the shared service connection as a
										fallback when a chat user hasn't
										completed their own personal OAuth.{" "}
										<em>
											Recommended only when the service
											account is a dedicated
											bifrost-service@ account, not a real
											user's.
										</em>
									</p>
								</div>
							</div>

							<div className="flex items-start gap-3">
								<Switch
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
										className="flex min-h-11 cursor-pointer items-center font-semibold"
									>
										Available to autonomous agents
									</Label>
									<p className="text-xs text-muted-foreground mt-1">
										Schedules and webhook-triggered runs use
										the shared service connection. Without
										this, autonomous agents cannot invoke
										this server's tools.
									</p>
								</div>
							</div>

							<p className="text-xs text-muted-foreground/80 pt-2">
								Both unchecked = personal-use only. Users still
								need to OAuth individually.
							</p>
						</CardContent>
					</Card>

					{/* Shared service connection */}
					<Card>
						<CardContent className="py-6 space-y-4">
							<h2 className="text-lg font-semibold">
								Shared service connection
							</h2>

							<div className="flex flex-wrap items-center justify-between gap-4">
								<div>
									{isConnected ? (
										<>
											<Badge
												variant="default"
												className="bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
											>
												Connected
											</Badge>
											<p className="text-xs text-muted-foreground mt-1">
												Service token linked. Refresh
												handled automatically by the
												OAuth refresh job.
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
												No shared service token. The
												chat / autonomous fallback flags
												above won't take effect until
												you connect.
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
											onClick={() =>
												setConnectModalOpen(true)
											}
										>
											{isConnected
												? "Reconnect"
												: "Connect"}
										</Button>
									)}
									{isConnected && (
										<Button
											variant="outline"
											className="text-destructive hover:text-destructive hover:bg-destructive/10"
											disabled={pagePending}
											onClick={handleDisconnect}
										>
											{serviceAction === "disconnect"
												? "Disconnecting…"
												: disconnectError
													? "Retry disconnect"
													: "Disconnect"}
										</Button>
									)}
								</div>
							</div>
							{disconnectError && (
								<p
									role="alert"
									className="text-sm text-destructive [overflow-wrap:anywhere]"
								>
									{disconnectError}
								</p>
							)}
						</CardContent>
					</Card>

					{/* Tool catalog */}
					<Card>
						<CardContent className="py-6 space-y-4">
							<div className="flex flex-wrap items-center justify-between gap-3">
								<h2 className="text-lg font-semibold">
									Tool catalog{" "}
									<Badge variant="secondary" className="ml-2">
										{(connection.tools ?? []).length} tools
										·{" "}
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
									type="button"
									className="min-h-11"
									disabled={
										refreshTools.isPending || !isConnected
									}
									onClick={handleRefreshTools}
									title={
										isConnected
											? "Re-fetch tools/list from the vendor"
											: "Connect first to refresh the catalog"
									}
								>
									{refreshTools.isPending ? (
										<Loader2 className="h-4 w-4 mr-1 animate-spin motion-reduce:animate-none" />
									) : null}
									{catalogError
										? "Retry catalog refresh"
										: "Refresh catalog"}
								</Button>
							</div>

							{catalogError && (
								<p
									role="alert"
									className="text-sm text-destructive [overflow-wrap:anywhere]"
								>
									{catalogError}
								</p>
							)}
							{(connection.tools ?? []).length === 0 ? (
								<p className="text-sm text-muted-foreground">
									No tools cached. Refresh the catalog after
									the service connection is healthy to
									populate.
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
									<div className="text-xs text-muted-foreground border-l-2 border-primary bg-primary/5 px-3 py-2 rounded-md">
										{isClientCredentials ? (
											<>
												<strong>
													Server-to-server auth:
												</strong>{" "}
												every tool call uses the shared
												service token (no per-user mode
												in client_credentials flow).
											</>
										) : availableInChat &&
										  availableToAutonomous ? (
											<>
												Tools resolve to the calling
												user's personal token if
												connected; fall back to the
												shared service token in chat and
												autonomous runs.
											</>
										) : availableInChat ? (
											<>
												Tools resolve to the calling
												user's personal token if
												connected; fall back to the
												shared service token in chat
												only.{" "}
												<em>
													Autonomous agent runs cannot
													use these tools until
													"Available to autonomous
													agents" is enabled.
												</em>
											</>
										) : availableToAutonomous ? (
											<>
												Tools resolve to the calling
												user's personal token if
												connected. Autonomous runs use
												the shared service token.{" "}
												<em>
													Chat users without a
													personal connection get a
													connect prompt.
												</em>
											</>
										) : (
											<>
												<strong>Per-user only:</strong>{" "}
												users must individually OAuth
												their account; no shared service
												fallback. Autonomous agent runs
												cannot use these tools.
											</>
										)}
									</div>
									<ConnectionToolCatalog
										tools={connection.tools ?? []}
										enabledMap={toolEnabledMap}
										onChange={(id, enabled) =>
											setToolEnabledMap((previous) => ({
												...previous,
												[id]: enabled,
											}))
										}
									/>
								</>
							)}
							<p className="text-xs text-muted-foreground">
								Catalog is per-connection: the vendor's
								tools/list response after this org's
								service-account OAuth. Other orgs may see
								different tools.
							</p>
						</CardContent>
					</Card>
				</fieldset>
				{saveError && (
					<Alert
						ref={saveErrorRef}
						tabIndex={-1}
						variant="destructive"
						className="outline-none"
					>
						<AlertDescription className="[overflow-wrap:anywhere]">
							{saveError}
						</AlertDescription>
					</Alert>
				)}
			</PageScrollArea>
			{/* Save / Cancel / Delete */}
			<div className="flex flex-wrap items-center gap-3 pt-2 [&_button]:min-h-11">
				<Button onClick={handleSave} disabled={pagePending}>
					{isSaving ? (
						<>
							<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
							Saving...
						</>
					) : saveError ? (
						"Retry save"
					) : (
						"Save"
					)}
				</Button>
				<Button
					variant="outline"
					disabled={pagePending}
					onClick={() => navigate(`/mcp-servers/${server.id}`)}
				>
					Cancel
				</Button>
				<div className="w-full sm:ml-auto sm:w-auto">
					<Button
						variant="outline"
						disabled={pagePending}
						className="w-full sm:w-auto text-destructive hover:bg-destructive/10 hover:text-destructive border-destructive/30"
						onClick={() => {
							deleteConnection.reset();
							setDeleteOpen(true);
						}}
					>
						<Trash2 className="h-4 w-4 mr-1" />
						Delete connection
					</Button>
				</div>
			</div>

			<AlertDialog
				open={deleteOpen}
				onOpenChange={(open) => {
					if (!deleteConnection.isPending) setDeleteOpen(open);
				}}
			>
				<AlertDialogContent className="flex max-h-[90dvh] flex-col overflow-hidden">
					<AlertDialogHeader className="min-h-0 overflow-y-auto">
						<AlertDialogTitle>
							Delete this connection?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently remove the connection, its
							cached tool catalog, and all per-user credentials
							linked to it. Agents using these tools will lose
							access immediately. This cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					{deleteConnection.isError && (
						<p role="alert" className="text-sm text-destructive">
							The connection could not be deleted. Try again.
						</p>
					)}
					<AlertDialogFooter className="shrink-0">
						<AlertDialogCancel
							className="min-h-11 lg:min-h-11"
							disabled={deleteConnection.isPending}
						>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={(event) => {
								event.preventDefault();
								void handleDelete();
							}}
							disabled={deleteConnection.isPending}
							variant="destructive"
							className="min-h-11"
						>
							{deleteConnection.isPending
								? "Deleting..."
								: deleteConnection.isError
									? "Retry deletion"
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
		</PageWorkspace>
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
	const [activationError, setActivationError] = useState<string | null>(null);

	const handleActivate = async () => {
		if (activating) return;
		setActivationError(null);
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
				setActivationError(detail);
				return;
			}
			if (data && "flow" in data && data.flow === "client_credentials") {
				toast.success("Connection activated");
			} else {
				// Backend returned an authorization_code response — the
				// server is misconfigured (provider flow_type was changed
				// after the form was rendered). Surface gracefully.
				setActivationError(
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
			setActivationError(
				err instanceof Error ? err.message : "Activation failed",
			);
		} finally {
			setActivating(false);
		}
	};

	return (
		<div className="min-w-0 space-y-3">
			{activationError && (
				<p
					role="alert"
					className="text-sm text-destructive [overflow-wrap:anywhere]"
				>
					{activationError}
				</p>
			)}
			<Button
				type="button"
				className="min-h-11"
				variant="outline"
				disabled={activating}
				onClick={handleActivate}
			>
				{activating ? (
					<>
						<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
						Activating...
					</>
				) : activationError ? (
					"Retry activation"
				) : isConnected ? (
					"Reactivate connection"
				) : (
					"Activate connection"
				)}
			</Button>
		</div>
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
	const [startError, setStartError] = useState<string | null>(null);

	const handleContinue = async () => {
		if (starting) return;
		setStartError(null);
		const popup = window.open(
			"about:blank",
			"_blank",
			"width=600,height=700",
		);
		if (!popup) {
			setStartError("Allow popups for Bifrost, then try again.");
			return;
		}
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
				throw new Error("Could not start authorization. Try again.");
			}

			if (popup.closed)
				throw new Error(
					"The sign-in window was closed. Try again to reopen it.",
				);
			popup.location.href = data.authorization_url;
			onOpenChange(false);
			toast.success(
				"Authorization started — complete it in the popup window",
			);
		} catch (err) {
			popup.close();
			setStartError(
				err instanceof Error
					? err.message
					: "Failed to start OAuth flow",
			);
		} finally {
			setStarting(false);
		}
	};

	return (
		<Dialog
			open={open}
			onOpenChange={(next) => {
				if (!starting) {
					setStartError(null);
					onOpenChange(next);
				}
			}}
		>
			<DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden">
				<DialogHeader className="shrink-0">
					<DialogTitle className="[overflow-wrap:anywhere]">
						Connect {serverName}
					</DialogTitle>
				</DialogHeader>

				<div className="min-h-0 overflow-y-auto space-y-3 [overflow-wrap:anywhere]">
					<p className="text-sm">
						You're about to authorize Bifrost to access {serverName}{" "}
						on your behalf.
					</p>

					<div className="rounded-[var(--bf-radius-surface)] border-l-4 border-[var(--bf-warning)] bg-[var(--bf-warning)]/10 p-3 text-sm">
						<p>
							<strong>This connection will be shared.</strong>{" "}
							Users will read and modify resources visible to{" "}
							<strong>{userEmail}</strong>'s account — recommended
							only for dedicated service accounts, not personal
							accounts.
						</p>
					</div>

					<p className="text-xs text-muted-foreground">
						Continuing will redirect you to the vendor's sign-in.
					</p>
				</div>

				{startError && (
					<p
						role="alert"
						className="text-sm text-destructive [overflow-wrap:anywhere]"
					>
						{startError}
					</p>
				)}
				<DialogFooter className="shrink-0">
					<Button
						type="button"
						className="min-h-11"
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={starting}
					>
						Cancel
					</Button>
					<Button
						type="button"
						className="min-h-11"
						onClick={handleContinue}
						disabled={starting}
					>
						{starting ? (
							<>
								<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
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
