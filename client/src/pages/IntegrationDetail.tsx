import { useState, useEffect, useMemo } from "react";
import { useParams, Link } from "react-router-dom";
import {
	XCircle,
	Loader2,
	Pencil,
	Code,
	Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
import { toast } from "sonner";
import {
	useIntegration,
	useUpdateMapping,
	useDeleteMapping,
	useUpdateIntegration,
	useUpdateIntegrationConfig,
	useTestIntegration,
	useBatchUpsertMappings,
	useCreateMapping,
	useAuthorizeMapping,
	useDisconnectMapping,
	useRefreshMapping,
	type IntegrationTestResponse,
} from "@/services/integrations";
import { $api } from "@/lib/api-client";
import { useQueryClient } from "@tanstack/react-query";
import {
	useAuthorizeOAuthConnection,
	useRefreshOAuthToken,
	useDeleteOAuthConnection,
} from "@/hooks/useOAuth";
import { isExpired, expiresSoon } from "@/lib/client-types";
import { CreateOAuthConnectionDialog } from "@/components/oauth/CreateOAuthConnectionDialog";
import { CreateIntegrationDialog } from "@/components/integrations/CreateIntegrationDialog";
import { OrgConfigDialog } from "@/components/integrations/OrgConfigDialog";
import { ConfigOverridesTab } from "@/components/integrations/ConfigOverridesTab";
import { useIntegrationEntities } from "@/hooks/useIntegrationEntities";
import { useAutoMatch } from "@/hooks/useAutoMatch";
import { GenerateSDKDialog } from "@/components/integrations/GenerateSDKDialog";
import { IntegrationOverview } from "@/components/integrations/IntegrationOverview";
import { IntegrationMappingsTab, type OrgWithMapping } from "@/components/integrations/IntegrationMappingsTab";
import { IntegrationTestPanel } from "@/components/integrations/IntegrationTestPanel";
import { IntegrationDefaultsDialog } from "@/components/integrations/IntegrationDefaultsDialog";

interface MappingFormData {
	organization_id: string;
	entity_id: string;
	entity_name: string;
	oauth_token_id?: string;
	config: Record<string, unknown>;
}

export function IntegrationDetail() {
	const { id: integrationId } = useParams<{ id: string }>();

	const [oauthConfigDialogOpen, setOAuthConfigDialogOpen] = useState(false);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [configDialogOpen, setConfigDialogOpen] = useState(false);
	const [defaultsDialogOpen, setDefaultsDialogOpen] = useState(false);
	const [defaultsFormValues, setDefaultsFormValues] = useState<
		Record<string, unknown>
	>({});
	const [selectedOrgForConfig, setSelectedOrgForConfig] = useState<
		OrgWithMapping | undefined
	>();
	const [deleteMappingConfirm, setDeleteMappingConfirm] =
		useState<OrgWithMapping | null>(null);
	const [editingOAuthConfig, setEditingOAuthConfig] = useState(false);
	const [deleteOAuthDialogOpen, setDeleteOAuthDialogOpen] = useState(false);
	const [generateSDKDialogOpen, setGenerateSDKDialogOpen] = useState(false);
	const [testDialogOpen, setTestDialogOpen] = useState(false);
	const [testOrgId, setTestOrgId] = useState<string | null>(null);
	const [testEndpoint, setTestEndpoint] = useState<string>("/");
	const [testResult, setTestResult] =
		useState<IntegrationTestResponse | null>(null);

	const queryClient = useQueryClient();

	// Fetch integration details (includes mappings and OAuth config)
	const {
		data: integration,
		isLoading: isLoadingIntegration,
		refetch: refetchIntegration,
	} = useIntegration(integrationId || "");

	// Fetch organizations
	const { data: orgsData, isLoading: isLoadingOrgs } = $api.useQuery(
		"get",
		"/api/organizations",
	);

	const updateMutation = useUpdateMapping();
	const deleteMutation = useDeleteMapping();
	const updateIntegrationMutation = useUpdateIntegration();
	const updateConfigMutation = useUpdateIntegrationConfig();
	const authorizeMutation = useAuthorizeOAuthConnection();
	const refreshMutation = useRefreshOAuthToken();
	const deleteOAuthMutation = useDeleteOAuthConnection();
	const testMutation = useTestIntegration();
	const batchMutation = useBatchUpsertMappings();
	const createMappingMutation = useCreateMapping();
	const authorizeMappingMutation = useAuthorizeMapping();
	const disconnectMappingMutation = useDisconnectMapping();
	const refreshMappingMutation = useRefreshMapping();
	const [refreshingMappingId, setRefreshingMappingId] = useState<
		string | null
	>(null);

	// Memoize to stabilize references for the useEffect that combines them
	const organizations = useMemo(
		() => (Array.isArray(orgsData) ? orgsData : []),
		[orgsData],
	);
	const mappings = useMemo(
		() => integration?.mappings || [],
		[integration?.mappings],
	);

	// Fetch entities from data provider
	const { data: entities = [], isLoading: isLoadingEntities, isError: isEntitiesError } =
		useIntegrationEntities(integration?.list_entities_data_provider_id);

	// Auto-match hook
	const {
		suggestions: autoMatchSuggestions,
		matchStats,
		isMatching,
		runAutoMatch,
		acceptSuggestion,
		rejectSuggestion,
		acceptAll,
		clearSuggestions,
	} = useAutoMatch({
		organizations: organizations.map(
			(org: { id: string; name: string }) => ({
				id: org.id,
				name: org.name,
			}),
		),
		entities: entities.map((e) => ({ value: e.value, label: e.label })),
		existingMappings: mappings
			.filter((m) => m.organization_id != null)
			.map((m) => ({
				organization_id: m.organization_id!,
				entity_id: m.entity_id,
			})),
	});

	// OAuth config from integration (now returned directly from GET /api/integrations/{id})
	const oauthConfig = integration?.oauth_config;

	// OAuth status helpers using the oauth_config from integration
	const isOAuthConnected = oauthConfig?.status === "completed";
	const isOAuthExpired =
		oauthConfig?.expires_at && isExpired(oauthConfig.expires_at);
	const isOAuthExpiringSoon =
		oauthConfig?.expires_at &&
		!isOAuthExpired &&
		expiresSoon(oauthConfig.expires_at, 15); // 15 minutes matches the refresh scheduler interval
	const canUseAuthCodeFlow =
		!!oauthConfig && oauthConfig.oauth_flow_type !== "client_credentials";

	// Combine organizations with their mappings using useMemo
	const orgsWithMappings = useMemo((): OrgWithMapping[] => {
		if (isLoadingOrgs || isLoadingIntegration) {
			return [];
		}
		return organizations.map((org: { id: string; name: string }) => {
			const existingMapping = mappings.find(
				(m) => m.organization_id === org.id,
			);

			const formData: MappingFormData = existingMapping
				? {
						organization_id: existingMapping.organization_id ?? org.id,
						entity_id: existingMapping.entity_id,
						entity_name: existingMapping.entity_name || "",
						oauth_token_id:
							existingMapping.oauth_token_id || undefined,
						config: existingMapping.config || {},
					}
				: {
						organization_id: org.id,
						entity_id: "",
						entity_name: "",
						config: {},
					};

			return {
				id: org.id,
				name: org.name,
				mapping: existingMapping,
				formData,
			};
		});
	}, [
		organizations,
		mappings,
		isLoadingOrgs,
		isLoadingIntegration,
	]);

	// Listen for OAuth success messages from popup window
	useEffect(() => {
		const handleMessage = (event: MessageEvent) => {
			// Verify origin for security
			if (event.origin !== window.location.origin) {
				return;
			}

			// Check if this is an OAuth success message
			if (event.data?.type === "oauth_success") {
				// Refresh integration (includes OAuth config)
				refetchIntegration();
				// Also refresh per-mapping data (entity_id, oauth_token_id) so
				// the table row updates without a manual page refresh.
				if (integrationId) {
					queryClient.invalidateQueries({
						queryKey: [
							"get",
							"/api/integrations/{integration_id}/mappings",
							{
								params: {
									path: { integration_id: integrationId },
								},
							},
						],
					});
				}
				toast.success("OAuth connection established successfully");
			}
		};

		window.addEventListener("message", handleMessage);

		// Cleanup listener on unmount
		return () => {
			window.removeEventListener("message", handleMessage);
		};
	}, [refetchIntegration, queryClient, integrationId]);

	const saveMappings = async (
		mappingsToSave: Array<{ organization_id: string; entity_id: string; entity_name?: string }>,
	) => {
		const result = await batchMutation.mutateAsync({
			params: { path: { integration_id: integrationId! } },
			body: { mappings: mappingsToSave },
		});
		const total = result.created + result.updated;
		const errorCount = result.errors?.length ?? 0;
		if (errorCount === 0) {
			toast.success(`Saved ${total} mapping(s)`);
		} else {
			toast.warning(`Saved ${total} mapping(s), ${errorCount} failed`);
		}
	};

	const handleEntitySelect = async (
		orgId: string,
		entityId: string,
		entityName?: string,
	) => {
		try {
			await saveMappings([{ organization_id: orgId, entity_id: entityId, entity_name: entityName }]);
		} catch {
			toast.error("Failed to save mapping");
		}
	};

	const handleDeleteMappingClick = (org: OrgWithMapping) => {
		setDeleteMappingConfirm(org);
	};

	const handleConnectMapping = async (org: OrgWithMapping) => {
		if (!integrationId) return;
		// Use the same redirect_uri shape as the integration-level Connect so the
		// shared OAuthCallback page (route /oauth/callback/:integrationId) handles
		// both flows. The state token carries mapping_id for the per-mapping path.
		const redirectUri = `${window.location.origin}/oauth/callback/${integrationId}`;

		// If there's no mapping row yet, create an empty one so the OAuth
		// callback has something to link the token to. entity_id will be
		// auto-populated from the callback when the provider has entity_id_source
		// configured.
		let mappingId = org.mapping?.id;
		if (!mappingId) {
			try {
				const created = await createMappingMutation.mutateAsync({
					params: { path: { integration_id: integrationId } },
					body: {
						organization_id: org.id,
						entity_id: org.formData.entity_id ?? "",
						entity_name: org.formData.entity_name ?? "",
					},
				});
				mappingId = created.id;
			} catch {
				toast.error("Failed to create mapping for OAuth connection");
				return;
			}
		}

		authorizeMappingMutation.mutate(
			{
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: mappingId,
					},
				},
				body: { redirect_uri: redirectUri },
			},
			{
				onSuccess: (response) => {
					// Match the integration-level Connect: open in a centered popup
					// so the user stays on the integration page and the existing
					// postMessage(oauth_success) listener refreshes state on close.
					const width = 600;
					const height = 700;
					const left =
						window.screenX + (window.outerWidth - width) / 2;
					const top =
						window.screenY + (window.outerHeight - height) / 2;
					window.open(
						response.authorization_url,
						"oauth_popup",
						`width=${width},height=${height},left=${left},top=${top},scrollbars=yes`,
					);
				},
				onError: () => {
					toast.error("Failed to start OAuth connection");
				},
			},
		);
	};

	const handleDisconnectMapping = (mappingId: string) => {
		if (!integrationId) return;
		disconnectMappingMutation.mutate(
			{
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: mappingId,
					},
				},
			},
			{
				onSuccess: () => {
					toast.success("OAuth connection disconnected");
				},
				onError: () => {
					toast.error("Failed to disconnect OAuth connection");
				},
			},
		);
	};

	const handleRefreshMapping = (mappingId: string) => {
		if (!integrationId) return;
		setRefreshingMappingId(mappingId);
		refreshMappingMutation.mutate(
			{
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: mappingId,
					},
				},
			},
			{
				onSuccess: () => {
					toast.success("Token refreshed");
				},
				onError: (err: unknown) => {
					const msg =
						(err as { detail?: string } | undefined)?.detail ??
						"Failed to refresh token";
					toast.error(msg);
				},
				onSettled: () => {
					setRefreshingMappingId(null);
				},
			},
		);
	};

	const handleDeleteMappingConfirm = async () => {
		const org = deleteMappingConfirm;
		if (!integrationId || !org?.mapping) {
			setDeleteMappingConfirm(null);
			return;
		}

		setDeleteMappingConfirm(null);

		try {
			await deleteMutation.mutateAsync({
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: org.mapping.id,
					},
				},
			});
			toast.success(`Mapping deleted for ${org.name}`);
			// Cache invalidation in useDeleteMapping handles refetch
		} catch (error) {
			console.error("Failed to delete mapping:", error);
			toast.error(`Failed to delete mapping for ${org.name}`);
		}
	};

	// Handle main integration OAuth connect
	const handleIntegrationOAuthConnect = async () => {
		if (!integration?.has_oauth_config || !integrationId) return;

		const redirectUri = `${window.location.origin}/oauth/callback/${integrationId}`;

		authorizeMutation.mutate(
			{
				params: {
					path: { connection_name: integrationId },
					query: { redirect_uri: redirectUri },
				},
			},
			{
				onSuccess: (response) => {
					const width = 600;
					const height = 700;
					const left =
						window.screenX + (window.outerWidth - width) / 2;
					const top =
						window.screenY + (window.outerHeight - height) / 2;
					window.open(
						response.authorization_url,
						"oauth_popup",
						`width=${width},height=${height},left=${left},top=${top},scrollbars=yes`,
					);
				},
			},
		);
	};

	// Handle main integration OAuth refresh
	const handleIntegrationOAuthRefresh = async () => {
		if (!integration?.has_oauth_config || !integrationId) return;

		try {
			await refreshMutation.mutateAsync({
				params: { path: { connection_name: integrationId } },
			});
			refetchIntegration();
			toast.success("Token refreshed successfully");
		} catch {
			// Error is already handled by the mutation's onError
		}
	};

	// Handle OAuth config deletion
	const handleDeleteOAuthConfig = async () => {
		if (!integrationId) return;

		try {
			await deleteOAuthMutation.mutateAsync({
				params: { path: { connection_name: integrationId } },
			});
			setDeleteOAuthDialogOpen(false);
			// The mutation's onSuccess already handles cache invalidation and toast
		} catch {
			// Error is already handled by the mutation's onError
		}
	};

	const handleOpenConfigDialog = (orgId: string) => {
		const org = orgsWithMappings.find((o) => o.id === orgId);
		if (org) {
			setSelectedOrgForConfig(org);
			setConfigDialogOpen(true);
		}
	};

	const handleSaveOrgConfig = async (config: Record<string, unknown>) => {
		if (!selectedOrgForConfig || !integrationId) return;

		// Only save if mapping exists (config is per-mapping)
		if (!selectedOrgForConfig.mapping) {
			toast.error("Save the mapping first before configuring");
			throw new Error("No mapping exists");
		}

		try {
			await updateMutation.mutateAsync({
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: selectedOrgForConfig.mapping.id,
					},
				},
				body: {
					entity_id: selectedOrgForConfig.formData.entity_id,
					entity_name:
						selectedOrgForConfig.formData.entity_name || undefined,
					oauth_token_id:
						selectedOrgForConfig.formData.oauth_token_id ||
						undefined,
					config: Object.keys(config).length > 0 ? config : undefined,
				},
			});
			toast.success(
				`Configuration saved for ${selectedOrgForConfig.name}`,
			);
			// Cache invalidation in useUpdateMapping handles refetch
		} catch (error) {
			console.error("Failed to save config:", error);
			toast.error(
				`Failed to save configuration for ${selectedOrgForConfig.name}`,
			);
			throw error; // Re-throw so dialog knows save failed
		}
	};

	// Configuration Defaults Dialog handlers
	const handleOpenDefaultsDialog = () => {
		if (!integration?.config_schema) return;
		// Initialize form with current default values from config_defaults
		const currentDefaults: Record<string, unknown> = {};
		integration.config_schema.forEach((field) => {
			currentDefaults[field.key] =
				integration.config_defaults?.[field.key] ?? "";
		});
		setDefaultsFormValues(currentDefaults);
		setDefaultsDialogOpen(true);
	};

	const handleSaveDefaults = async () => {
		if (!integrationId || !integration?.config_schema) return;

		// Validate form values before save
		const validationErrors: string[] = [];
		for (const field of integration.config_schema) {
			const value = defaultsFormValues[field.key];

			// Skip empty values (they're allowed)
			if (value === "" || value === null || value === undefined) {
				continue;
			}

			// Validate int fields
			if (field.type === "int") {
				const numValue =
					typeof value === "string" ? parseInt(value) : value;
				if (isNaN(numValue as number)) {
					validationErrors.push(
						`${field.key} must be a valid integer`,
					);
				}
			}

			// Validate JSON fields
			if (field.type === "json") {
				if (typeof value === "string") {
					try {
						JSON.parse(value);
					} catch {
						validationErrors.push(
							`${field.key} must be valid JSON`,
						);
					}
				}
			}
		}

		if (validationErrors.length > 0) {
			toast.error(validationErrors.join(", "));
			return;
		}

		try {
			// Build config object from form values
			const config: Record<string, unknown> = {};
			for (const [key, value] of Object.entries(defaultsFormValues)) {
				// Only include non-empty values
				if (value !== "" && value !== null && value !== undefined) {
					config[key] = value;
				}
			}

			await updateConfigMutation.mutateAsync({
				params: { path: { integration_id: integrationId } },
				body: { config },
			});

			toast.success("Configuration defaults updated");
			setDefaultsDialogOpen(false);
		} catch (error) {
			console.error("Failed to update defaults:", error);
			toast.error("Failed to update configuration defaults");
		}
	};

	const handleAcceptSuggestion = async (orgId: string) => {
		const suggestion = acceptSuggestion(orgId);
		if (suggestion) {
			try {
				await saveMappings([{
					organization_id: orgId,
					entity_id: suggestion.entityId,
					entity_name: suggestion.entityName,
				}]);
			} catch {
				toast.error("Failed to save mapping");
			}
		}
	};

	const handleAcceptAllSuggestions = async () => {
		const suggestions = acceptAll();
		if (suggestions.length === 0) return;
		try {
			await saveMappings(
				suggestions.map((s) => ({
					organization_id: s.organizationId,
					entity_id: s.entityId,
					entity_name: s.entityName,
				})),
			);
		} catch {
			toast.error("Failed to save mappings");
		}
	};

	// Handle test connection
	const handleTestConnection = async () => {
		if (!integrationId) return;

		setTestResult(null);

		try {
			const result = await testMutation.mutateAsync({
				params: { path: { integration_id: integrationId } },
				body: { organization_id: testOrgId, endpoint: testEndpoint },
			});
			setTestResult(result);
			if (result.success) {
				toast.success(result.message);
			} else {
				toast.error(result.message);
			}
		} catch (error) {
			console.error("Test connection failed:", error);
			toast.error("Failed to test connection");
		}
	};

	const handleOpenTestDialog = () => {
		// Default to Global (null) - tests with integration defaults only
		setTestOrgId(null);
		setTestEndpoint("/");
		setTestResult(null);
		setTestDialogOpen(true);
	};

	const isLoading = isLoadingIntegration || isLoadingOrgs;

	if (isLoading) {
		return (
			<div className="space-y-6">
				<Skeleton className="h-12 w-64" />
				<Skeleton className="h-64 w-full" />
			</div>
		);
	}

	if (!integration) {
		return (
			<div className="flex flex-col items-center justify-center py-12">
				<XCircle className="h-12 w-12 text-destructive" />
				<h3 className="mt-4 text-lg font-semibold">
					Integration not found
				</h3>
				<Link to="/integrations">
					<Button variant="outline" className="mt-4">
						Back to Integrations
					</Button>
				</Link>
			</div>
		);
	}

	return (
		<div className="space-y-6">
			{/* Header */}
			<div>
				<div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
					<Link
						to="/integrations"
						className="hover:text-foreground transition-colors"
					>
						Integrations
					</Link>
					<span>/</span>
					<span>{integration.name}</span>
				</div>
				<div className="flex items-center justify-between">
					<div>
						<h1 className="text-4xl font-extrabold tracking-tight">
							{integration.name}
						</h1>
						<p className="mt-2 text-muted-foreground">
							{(
								integration as typeof integration & {
									description?: string;
								}
							).description ||
								"Configure OAuth, data providers, and organization mappings"}
						</p>
					</div>
					<div className="flex items-center gap-2">
						<Button
							variant="outline"
							size="sm"
							onClick={handleOpenTestDialog}
							title="Test integration connection"
						>
							<Zap className="h-4 w-4 mr-2" />
							Test Connection
						</Button>
						<Button
							variant="outline"
							size="sm"
							onClick={() => setGenerateSDKDialogOpen(true)}
							title="Generate SDK from OpenAPI spec"
						>
							<Code className="h-4 w-4 mr-2" />
							Generate SDK
						</Button>
						<Button
							variant="outline"
							size="icon"
							onClick={() => setEditDialogOpen(true)}
							title="Edit integration"
						>
							<Pencil className="h-4 w-4" />
						</Button>
					</div>
				</div>
			</div>

			{/* Config Defaults & OAuth Status */}
			<IntegrationOverview
				integration={integration}
				oauthConfig={oauthConfig}
				isOAuthConnected={isOAuthConnected}
				isOAuthExpired={isOAuthExpired}
				isOAuthExpiringSoon={isOAuthExpiringSoon}
				canUseAuthCodeFlow={canUseAuthCodeFlow}
				onOpenDefaultsDialog={handleOpenDefaultsDialog}
				onOAuthConnect={handleIntegrationOAuthConnect}
				onOAuthRefresh={handleIntegrationOAuthRefresh}
				onEditOAuthConfig={() => setEditingOAuthConfig(true)}
				onDeleteOAuthConfig={() => setDeleteOAuthDialogOpen(true)}
				onCreateOAuthConfig={() => setOAuthConfigDialogOpen(true)}
				isAuthorizePending={authorizeMutation.isPending}
				isRefreshPending={refreshMutation.isPending}
			/>

			{/* Tabs for Mappings and Config Overrides */}
			<Tabs defaultValue="mappings" className="space-y-4">
				<TabsList>
					<TabsTrigger value="mappings">Mappings</TabsTrigger>
					<TabsTrigger value="config-overrides">
						Config Overrides
					</TabsTrigger>
				</TabsList>

				<TabsContent value="mappings">
					<IntegrationMappingsTab
						orgsWithMappings={orgsWithMappings}
						entities={entities}
						isLoadingEntities={isLoadingEntities}
						isEntitiesError={isEntitiesError}
						hasDataProvider={!!integration.list_entities_data_provider_id}
						hasOAuth={!!integration.has_oauth_config}
						configSchema={integration?.config_schema || []}
						configDefaults={integration?.config_defaults}
						autoMatchSuggestions={autoMatchSuggestions}
						matchStats={matchStats}
						isMatching={isMatching}
						isDeletePending={deleteMutation.isPending}
						onRunAutoMatch={runAutoMatch}
						onAcceptAllSuggestions={handleAcceptAllSuggestions}
						onClearSuggestions={clearSuggestions}
						onAcceptSuggestion={handleAcceptSuggestion}
						onRejectSuggestion={rejectSuggestion}
						onUpdateOrgMapping={handleEntitySelect}
						onOpenConfigDialog={handleOpenConfigDialog}
						onDeleteMapping={handleDeleteMappingClick}
						onConnectMapping={handleConnectMapping}
						onDisconnectMapping={handleDisconnectMapping}
						onRefreshMapping={handleRefreshMapping}
						refreshingMappingId={refreshingMappingId}
					/>
				</TabsContent>

				<TabsContent value="config-overrides">
					<Card>
						<CardHeader>
							<CardTitle>Configuration Overrides</CardTitle>
							<CardDescription>
								Manage organization-specific configuration
								overrides
							</CardDescription>
						</CardHeader>
						<CardContent>
							<ConfigOverridesTab
								orgsWithMappings={orgsWithMappings}
								configSchema={integration?.config_schema || []}
								integrationId={integrationId || ""}
							/>
						</CardContent>
					</Card>
				</TabsContent>
			</Tabs>

			{/* OAuth Configuration Dialog (Create) */}
			{integrationId && (
				<CreateOAuthConnectionDialog
					open={oauthConfigDialogOpen}
					onOpenChange={setOAuthConfigDialogOpen}
					integrationId={integrationId}
				/>
			)}

			{/* OAuth Configuration Dialog (Edit) */}
			{integrationId && (
				<CreateOAuthConnectionDialog
					open={editingOAuthConfig}
					onOpenChange={setEditingOAuthConfig}
					integrationId={integrationId}
					editConnectionName={integrationId}
				/>
			)}

			{/* Edit Integration Dialog */}
			<CreateIntegrationDialog
				open={editDialogOpen}
				onOpenChange={setEditDialogOpen}
				editIntegrationId={integrationId}
				initialData={integration}
			/>

			{/* Org Config Dialog */}
			{selectedOrgForConfig && (
				<OrgConfigDialog
					open={configDialogOpen}
					onOpenChange={setConfigDialogOpen}
					orgId={selectedOrgForConfig.id}
					orgName={selectedOrgForConfig.name}
					configSchema={integration?.config_schema || []}
					currentConfig={selectedOrgForConfig.formData.config}
					onSave={handleSaveOrgConfig}
				/>
			)}

			{/* Configuration Defaults Dialog */}
			<IntegrationDefaultsDialog
				open={defaultsDialogOpen}
				onOpenChange={setDefaultsDialogOpen}
				configSchema={integration?.config_schema || []}
				formValues={defaultsFormValues}
				onFormValuesChange={setDefaultsFormValues}
				onSave={handleSaveDefaults}
				isSaving={updateIntegrationMutation.isPending}
			/>

			{/* Delete Mapping Confirmation Dialog */}
			<AlertDialog
				open={deleteMappingConfirm !== null}
				onOpenChange={(open) => !open && setDeleteMappingConfirm(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Mapping</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete the mapping for{" "}
							<span className="font-semibold">
								{deleteMappingConfirm?.name}
							</span>
							? This will remove the organization's integration
							configuration and cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDeleteMappingConfirm}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Delete OAuth Configuration Confirmation Dialog */}
			<AlertDialog
				open={deleteOAuthDialogOpen}
				onOpenChange={setDeleteOAuthDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete OAuth Configuration
						</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete the OAuth
							configuration for{" "}
							<span className="font-semibold">
								{integration?.name}
							</span>
							? This will remove the OAuth connection and any
							stored tokens. This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel
							disabled={deleteOAuthMutation.isPending}
						>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDeleteOAuthConfig}
							disabled={deleteOAuthMutation.isPending}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteOAuthMutation.isPending ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Deleting...
								</>
							) : (
								"Delete"
							)}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Generate SDK Dialog */}
			{integrationId && (
				<GenerateSDKDialog
					open={generateSDKDialogOpen}
					onOpenChange={setGenerateSDKDialogOpen}
					integrationId={integrationId}
					integrationName={integration?.name || ""}
					hasOAuth={integration?.has_oauth_config || false}
				/>
			)}

			{/* Test Connection Dialog */}
			<IntegrationTestPanel
				open={testDialogOpen}
				onOpenChange={setTestDialogOpen}
				testOrgId={testOrgId}
				onTestOrgIdChange={setTestOrgId}
				testEndpoint={testEndpoint}
				onTestEndpointChange={setTestEndpoint}
				testResult={testResult}
				onClearResult={() => setTestResult(null)}
				onTest={handleTestConnection}
				isTestPending={testMutation.isPending}
			/>
		</div>
	);
}
