/**
 * MCPServerDetail — server detail with Connections / Server settings / Manifest
 * tabs (mockup §4).
 *
 * Connections tab is the default — that's where 99% of admin work happens.
 * Server settings + Manifest are placeholders for now.
 */

import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, RefreshCw, Trash2 } from "lucide-react";

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
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { $api } from "@/lib/api-client";
import { useOrganizations } from "@/hooks/useOrganizations";
import { toast } from "sonner";

export function MCPServerDetail() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const queryClient = useQueryClient();

	const {
		data: server,
		isLoading,
	} = $api.useQuery(
		"get",
		"/api/mcp-servers/{server_id}",
		{ params: { path: { server_id: id! } } },
		{ enabled: !!id },
	);

	const { data: organizations = [] } = useOrganizations();
	const orgById = useMemo(() => {
		const map = new Map<string, string>();
		for (const o of organizations) map.set(o.id, o.name);
		return map;
	}, [organizations]);

	const [createOpen, setCreateOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);

	const deleteServer = $api.useMutation(
		"delete",
		"/api/mcp-servers/{server_id}",
	);

	const handleDelete = async () => {
		if (!id) return;
		try {
			await deleteServer.mutateAsync({
				params: { path: { server_id: id }, query: { hard: true } },
			});
			toast.success("MCP server deleted");
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-servers"],
			});
			navigate("/mcp-servers");
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Failed to delete server",
			);
		} finally {
			setDeleteOpen(false);
		}
	};

	if (isLoading) {
		return (
			<div className="space-y-4 max-w-7xl mx-auto">
				<Skeleton className="h-10 w-1/3" />
				<Skeleton className="h-32 w-full" />
			</div>
		);
	}

	if (!server) {
		return (
			<Card className="max-w-3xl mx-auto">
				<CardContent className="py-12 text-center">
					<p className="text-muted-foreground">
						MCP server not found.
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

	return (
		<div className="space-y-6 max-w-7xl mx-auto">
			{/* Breadcrumb / header */}
			<div className="space-y-2">
				<button
					type="button"
					onClick={() => navigate("/mcp-servers")}
					className="text-sm text-blue-600 hover:underline inline-flex items-center"
				>
					<ArrowLeft className="h-3.5 w-3.5 mr-1" />
					MCP Servers
				</button>
				<div className="flex items-center justify-between">
					<div>
						<h1 className="text-3xl font-extrabold tracking-tight">
							{server.name}
						</h1>
						<div className="mt-1 flex items-center gap-2">
							<code className="text-xs text-muted-foreground break-all">
								{server.server_url}
							</code>
							{server.is_active ? (
								<Badge
									variant="default"
									className="bg-green-600 hover:bg-green-700"
								>
									Active
								</Badge>
							) : (
								<Badge variant="secondary">Inactive</Badge>
							)}
						</div>
					</div>
					<Button
						variant="outline"
						size="sm"
						className="text-rose-600 hover:bg-rose-50 hover:text-rose-700 border-rose-200"
						onClick={() => setDeleteOpen(true)}
					>
						<Trash2 className="h-4 w-4 mr-1" />
						Delete server
					</Button>
				</div>
			</div>

			<Tabs defaultValue="connections" className="w-full">
				<TabsList>
					<TabsTrigger value="connections">Connections</TabsTrigger>
					<TabsTrigger value="settings">Server settings</TabsTrigger>
					<TabsTrigger value="manifest">Manifest</TabsTrigger>
				</TabsList>

				<TabsContent value="connections" className="space-y-4 pt-4">
					<div className="flex items-center justify-between">
						<p className="text-sm text-muted-foreground">
							Per-org connections to this server. Each org sets
							its own OAuth app credentials.
						</p>
						<div className="flex gap-2">
							<Button
								variant="outline"
								size="sm"
								onClick={() => {
									queryClient.invalidateQueries({
										queryKey: [
											"get",
											"/api/mcp-servers/{server_id}",
										],
									});
								}}
							>
								<RefreshCw className="h-4 w-4 mr-1" />
								Refresh
							</Button>
							<Button
								size="sm"
								onClick={() => setCreateOpen(true)}
							>
								<Plus className="h-4 w-4 mr-1" />
								New Connection
							</Button>
						</div>
					</div>

					{server.connections && server.connections.length > 0 ? (
						<DataTable>
							<DataTableHeader>
								<DataTableRow>
									<DataTableHead>Organization</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">
										Status
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">
										Tools cached
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap text-center">
										User chat
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap text-center">
										Autonomous
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap text-right" />
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{server.connections.map((conn) => {
									const orgName =
										orgById.get(conn.organization_id) ??
										conn.organization_id.slice(0, 8);
									const tools = conn.tools ?? [];
									return (
										<DataTableRow
											key={conn.id}
											clickable
											onClick={() =>
												navigate(
													`/mcp-servers/${server.id}/connections/${conn.id}/edit`,
												)
											}
										>
											<DataTableCell className="font-medium">
												{orgName}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap">
												{conn.service_oauth_token_id ? (
													<Badge
														variant="default"
														className="bg-green-600 hover:bg-green-700"
													>
														Connected
													</Badge>
												) : (
													<Badge
														variant="default"
														className="bg-amber-600 hover:bg-amber-700"
													>
														No service connection
													</Badge>
												)}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap">
												{tools.length} tools
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-center">
												{conn.available_in_chat
													? "✓"
													: "✗"}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-center">
												{conn.available_to_autonomous
													? "✓"
													: "✗"}
											</DataTableCell>
											<DataTableCell
												className="w-0 whitespace-nowrap text-right"
												onClick={(e) =>
													e.stopPropagation()
												}
											>
												<Button
													variant="link"
													size="sm"
													onClick={() =>
														navigate(
															`/mcp-servers/${server.id}/connections/${conn.id}/edit`,
														)
													}
												>
													Manage
												</Button>
											</DataTableCell>
										</DataTableRow>
									);
								})}
							</DataTableBody>
						</DataTable>
					) : (
						<Card>
							<CardContent className="py-8 text-center text-sm text-muted-foreground">
								No connections yet. Click "New Connection" to
								add one.
							</CardContent>
						</Card>
					)}
				</TabsContent>

				<TabsContent value="settings" className="pt-4">
					<Card>
						<CardContent className="py-6 space-y-4">
							<div>
								<Label className="text-xs text-muted-foreground">
									Name
								</Label>
								<p className="font-medium">{server.name}</p>
							</div>
							<div>
								<Label className="text-xs text-muted-foreground">
									Server URL
								</Label>
								<code className="block text-xs break-all">
									{server.server_url}
								</code>
							</div>
							<div>
								<Label className="text-xs text-muted-foreground">
									Redirect URL
								</Label>
								<code className="block text-xs break-all">
									{server.redirect_url ?? (
										<span className="text-muted-foreground">
											Not set
										</span>
									)}
								</code>
							</div>
							<div>
								<Label className="text-xs text-muted-foreground">
									OAuth provider
								</Label>
								<p className="text-sm">
									{server.oauth_provider_id ? (
										<code className="text-xs">
											{server.oauth_provider_id}
										</code>
									) : (
										<span className="text-muted-foreground">
											None — cannot start service-token
											flow until linked
										</span>
									)}
								</p>
							</div>
							<div>
								<Label className="text-xs text-muted-foreground">
									Discovery metadata
								</Label>
								<pre className="mt-1 text-xs bg-muted p-2 rounded max-h-72 overflow-auto">
									{JSON.stringify(
										server.discovery_metadata ?? null,
										null,
										2,
									)}
								</pre>
							</div>
						</CardContent>
					</Card>
				</TabsContent>

				<TabsContent value="manifest" className="pt-4">
					<Card>
						<CardContent className="py-6">
							<p className="text-sm text-muted-foreground">
								Manifest export/import for this server template
								is round-tripped through{" "}
								<code className="text-xs">
									.bifrost/mcp-servers.yaml
								</code>{" "}
								during a global manifest sync. Per-server export
								here is a future enhancement.
							</p>
						</CardContent>
					</Card>
				</TabsContent>
			</Tabs>

			<NewConnectionDialog
				open={createOpen}
				onOpenChange={setCreateOpen}
				serverId={server.id}
			/>

			<AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete this MCP server?</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently remove the server template{" "}
							<strong>{server.name}</strong> and cascade-delete all{" "}
							connections, cached tool catalogs, and per-user
							credentials linked to it. Agents using these tools
							will lose access immediately. This cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={deleteServer.isPending}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDelete}
							disabled={deleteServer.isPending}
							className="bg-rose-600 hover:bg-rose-700 text-white"
						>
							{deleteServer.isPending ? "Deleting..." : "Delete server"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}

/**
 * Lightweight new-connection dialog: pick org + initial credentials.
 * The full edit page (with availability flags + tool catalog) opens
 * automatically after create.
 */
function NewConnectionDialog({
	open,
	onOpenChange,
	serverId,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	serverId: string;
}) {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const { data: organizations = [] } = useOrganizations();

	const [orgId, setOrgId] = useState<string>("");
	const [clientId, setClientId] = useState("");
	const [clientSecret, setClientSecret] = useState("");

	const create = $api.useMutation("post", "/api/mcp-connections");

	const handleCreate = async () => {
		if (!orgId || !clientId || !clientSecret) {
			toast.error("Organization, client ID, and client secret are required");
			return;
		}
		try {
			const result = await create.mutateAsync({
				body: {
					server_id: serverId,
					organization_id: orgId,
					client_id: clientId,
					client_secret: clientSecret,
					available_in_chat: false,
					available_to_autonomous: false,
				},
			});
			toast.success("Connection created");
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-servers/{server_id}"],
			});
			onOpenChange(false);
			navigate(
				`/mcp-servers/${serverId}/connections/${result.id}/edit`,
			);
		} catch (err) {
			toast.error(
				err instanceof Error ? err.message : "Failed to create connection",
			);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>New MCP Connection</DialogTitle>
					<DialogDescription>
						Link this server template to an organization. You'll
						configure availability and OAuth on the next page.
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-4">
					<div className="space-y-2">
						<Label>Organization</Label>
						<Select value={orgId} onValueChange={setOrgId}>
							<SelectTrigger>
								<SelectValue placeholder="Select organization..." />
							</SelectTrigger>
							<SelectContent>
								{organizations.map((o) => (
									<SelectItem key={o.id} value={o.id}>
										{o.name}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>

					<div className="space-y-2">
						<Label htmlFor="client_id">Client ID</Label>
						<Input
							id="client_id"
							value={clientId}
							onChange={(e) => setClientId(e.target.value)}
							placeholder="abc123..."
							className="font-mono"
						/>
					</div>

					<div className="space-y-2">
						<Label htmlFor="client_secret">Client Secret</Label>
						<Input
							id="client_secret"
							type="password"
							value={clientSecret}
							onChange={(e) => setClientSecret(e.target.value)}
							placeholder="••••••••••••••••"
						/>
					</div>
				</div>

				<DialogFooter>
					<Button
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={create.isPending}
					>
						Cancel
					</Button>
					<Button
						onClick={handleCreate}
						disabled={create.isPending}
					>
						{create.isPending ? "Creating..." : "Create"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
